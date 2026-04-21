import requests
from bs4 import BeautifulSoup
import re
import json
import urllib.parse
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
import random
import os
from curl_cffi import requests as cffi_requests

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

# ─────────────────────────────────────────────
# CATEGORY MAPPINGS (strict keyword matching)
# ─────────────────────────────────────────────

GENERIC_CATEGORIES = {
    "phone": ["phone", "mobile", "smartphone", "cellphone"],
    "tv": ["tv", "television", "led", "lcd", "oled", "qled", "ultra hd"],
    "laptop": ["laptop", "notebook"],
    "ac": ["ac", "air conditioner", "aircon", "inverter ac"],
    "refrigerator": ["refrigerator", "fridge", "freezer"],
    "washing machine": ["washing machine", "washer", "fully automatic"],
    "headphones": ["headphones", "earphones", "earbuds", "neckband"],
    "smartwatch": ["smartwatch", "smart watch", "fitness tracker"],
    "tablet": ["tablet", "ipad", "android pad"],
    "monitor": ["monitor", "display", "screen"],
    "printer": ["printer", "all-in-one", "inkjet", "laser printer"],
    "microwave": ["microwave", "microwave oven"],
    "mixer": ["mixer", "mixer grinder", "juicer"],
    "fan": ["fan", "ceiling fan", "pedestal fan", "table fan"],
    "cooler": ["cooler", "air cooler", "desert cooler"],
    "geyser": ["geyser", "water heater"],
    "iron": ["iron", "garment steamer", "press"],
    "vacuum cleaner": ["vacuum cleaner", "vacuum", "cleaner", "robot vacuum"],
    "trimmer": ["trimmer", "hair trimmer", "beard trimmer"],
    "shaver": ["shaver", "electric shaver", "razor"],
    "kettle": ["kettle", "electric kettle"],
    "toaster": ["toaster", "bread toaster"],
    "induction cooktop": ["induction", "induction cooktop", "induction stove"],
    "samsung galaxy": ["samsung galaxy"],
    "iphone": ["iphone"],
    "oneplus": ["oneplus"],
    "pixel": ["pixel", "google pixel"],
    "realme": ["realme"],
    "redmi": ["redmi", "xiaomi"],
    "oppo": ["oppo"],
    "vivo": ["vivo"],
    "motorola": ["motorola", "moto"],
    "nokia": ["nokia"],
    "asus": ["asus"],
    "nothing": ["nothing phone"],
}

def is_generic_category(query):
    q_lower = query.lower().strip()
    for cat in GENERIC_CATEGORIES:
        if q_lower == cat or q_lower.startswith(cat) or cat in q_lower.split():
            return True
    if len(q_lower.split()) <= 4 and not any(char.isdigit() for char in q_lower):
        known_brands = ['iphone', 'samsung', 'oneplus', 'pixel', 'realme', 'redmi',
                        'xiaomi', 'oppo', 'vivo', 'motorola', 'nokia', 'asus', 'nothing']
        if not any(brand in q_lower for brand in known_brands):
            return True
    return False

def is_correct_product(query, title):
    if not query or not title:
        return False
    q_lower = query.lower()
    t_lower = title.lower()

    # Accessories filter
    accessories = ['case', 'cover', 'glass', 'protector', 'cable', 'charger',
                   'adapter', 'skin', 'stand', 'mount', 'holder', 'pouch',
                   'sleeve', 'bumper', 'shield', 'wrap', 'film', 'foil', 'housing',
                   'sticker', 'decal', 'toy model', 'miniature', 'installation service']
    if not any(a in q_lower for a in accessories):
        if any(a in t_lower for a in accessories):
            return False

    # Brand enforcement
    key_brands = ['iphone', 'samsung', 'oneplus', 'pixel', 'realme', 'redmi',
                  'xiaomi', 'oppo', 'vivo', 'motorola', 'nokia', 'sony', 'asus', 'nothing']
    for brand in key_brands:
        if brand in q_lower and brand not in t_lower:
            return False

    # Generic query handling
    if is_generic_category(q_lower):
        matched_category = None
        for cat, keywords in GENERIC_CATEGORIES.items():
            if cat in q_lower or any(kw in q_lower for kw in keywords):
                matched_category = keywords
                break
        if matched_category:
            if any(kw in t_lower for kw in matched_category):
                return True
            # Fallback: check common words or numbers
            q_words = set(re.findall(r'\b[a-z0-9]+\b', q_lower))
            t_words = set(re.findall(r'\b[a-z0-9]+\b', t_lower))
            common = {w for w in q_words.intersection(t_words) if len(w) > 2}
            if common:
                return True
            q_nums = re.findall(r'\d+', q_lower)
            t_nums = re.findall(r'\d+', t_lower)
            if set(q_nums).intersection(t_nums):
                return True
            print(f"    [Filter] Generic '{query}' – no match: {title[:50]}")
            return False
        else:
            # No category known – accept if any common word
            q_words = set(re.findall(r'\b[a-z0-9]+\b', q_lower))
            t_words = set(re.findall(r'\b[a-z0-9]+\b', t_lower))
            if q_words.intersection(t_words):
                return True
            print(f"    [Filter] Generic '{query}' – no common words: {title[:50]}")
            return False

    # Specific query – accept
    return True

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def clean_price(price_str):
    if not price_str:
        return float('inf')
    cleaned = re.sub(r'[^\d.]', '', str(price_str))
    try:
        val = float(cleaned)
        return val if val > 10 else float('inf')
    except:
        return float('inf')

def _cffi_get(url, timeout=14, extra_headers=None):
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "User-Agent": random.choice(USER_AGENTS),
        **(extra_headers or {}),
    }
    for browser in [random.choice(["chrome124", "chrome123", "chrome120"]), "chrome116"]:
        try:
            r = cffi_requests.get(url, impersonate=browser, timeout=timeout, headers=headers)
            if r.status_code == 200:
                return r
        except Exception as e:
            print(f"    [{browser}] {e}")
    return None

# ─────────────────────────────────────────────
# SCRAPERS (all six stores)
# ─────────────────────────────────────────────

def scrape_amazon(query):
    safe_q = urllib.parse.quote(query)
    url = f"https://www.amazon.in/s?k={safe_q}"
    try:
        resp = _cffi_get(url, extra_headers={"Referer": "https://www.amazon.in/"})
        if not resp:
            return None
        print(f"  [Amazon] Status: {resp.status_code}")

        page_lower = resp.text.lower()
        if 's-search-result' not in resp.text or 'captcha' in page_lower or 'robot check' in page_lower:
            print("  [Amazon] Blocked/CAPTCHA")
            return None

        soup = BeautifulSoup(resp.text, 'lxml')
        cards = soup.find_all('div', {'data-component-type': 's-search-result'})
        print(f"  [Amazon] Cards found: {len(cards)}")

        def is_sponsored(card):
            return card.get('data-component-type') == 'sp-sponsored-result' or card.find('span', string=re.compile(r'^Sponsored$', re.I))

        def parse_card(card):
            title_elem = card.select_one('h2 span')
            if not title_elem:
                return None
            title_text = title_elem.get_text(strip=True)
            if len(title_text) < 5:
                return None

            price = float('inf')
            pe = card.select_one('.a-price-whole')
            if pe:
                price = clean_price(pe.get_text(strip=True))
            if price == float('inf'):
                os_e = card.select_one('.a-price .a-offscreen')
                if os_e:
                    price = clean_price(os_e.get_text(strip=True))
            if price == float('inf'):
                m = re.search(r'₹\s*([\d,]+)', card.get_text())
                if m:
                    price = clean_price(m.group(1))
            if price == float('inf'):
                return None

            print(f"  [Amazon] Candidate: ₹{price:,.0f} | {title_text[:60]}")
            if not is_correct_product(query, title_text):
                return None

            rating_val = "N/A"
            for sel in ['span.a-icon-alt', 'i[class*="a-star"] span']:
                elem = card.select_one(sel)
                if elem:
                    m = re.search(r'([1-5]\.\d)', elem.get_text())
                    if m:
                        rating_val = m.group(1)
                        break

            link_elem = card.select_one('a[href*="/dp/"]')
            link = urljoin("https://www.amazon.in", link_elem.get('href', '#')) if link_elem else url
            img_elem = card.select_one('img.s-image')
            img = img_elem.get('src', '') if img_elem else ''
            print(f"  [Amazon] ✅ {title_text[:50]} @ ₹{price:,.0f} ⭐{rating_val}")
            return {"price": price, "display_price": f"₹{price:,.0f}",
                    "rating": rating_val, "link": link, "title": title_text[:70], "image": img}

        for card in [c for c in cards if not is_sponsored(c)]:
            r = parse_card(card)
            if r:
                return r
        print("  [Amazon] Trying sponsored...")
        for card in [c for c in cards if is_sponsored(c)]:
            r = parse_card(card)
            if r:
                return r

    except Exception as e:
        print(f"  [Amazon] Error: {e}")
    return None

def scrape_flipkart(query):
    safe_q = urllib.parse.quote(query)
    url = f"https://www.flipkart.com/search?q={safe_q}"
    try:
        resp = _cffi_get(url)
        if not resp:
            return None
        print(f"  [Flipkart] Status: {resp.status_code}")
        soup = BeautifulSoup(resp.text, 'lxml')
        product_links = soup.select('a[href*="/p/"]')
        print(f"  [Flipkart] Product links: {len(product_links)}")

        for link in product_links:
            text_content = link.get_text(' ', strip=True)
            if '₹' not in text_content:
                continue
            price_match = re.search(r'₹\s*([0-9,]+)', text_content)
            if not price_match:
                continue
            price = clean_price(price_match.group(1))
            if price == float('inf'):
                continue

            title_text = ""
            img = link.select_one('img')
            if img and len(img.get('alt', '')) > 5:
                title_text = img.get('alt')
            if not title_text:
                for d in link.find_all('div'):
                    t = d.get_text(strip=True)
                    if len(t) > 10 and '₹' not in t and not re.match(r'^[1-5]\.[0-9]', t):
                        title_text = t
                        break
            if not title_text:
                continue

            print(f"  [Flipkart] Candidate: ₹{price:,.0f} | {title_text[:60]}")
            if not is_correct_product(query, title_text):
                continue

            rating_val = "N/A"
            for div in link.find_all('div'):
                if re.match(r'^[1-5]\.[0-9]$', div.get_text(strip=True)):
                    rating_val = div.get_text(strip=True)
                    break

            fk_img = ''
            if img:
                fk_img = img.get('src') or img.get('data-src') or ''

            print(f"  [Flipkart] ✅ {title_text[:50]} @ ₹{price:,.0f} ⭐{rating_val}")
            return {"price": price, "display_price": f"₹{price:,.0f}", "rating": rating_val,
                    "link": urljoin("https://www.flipkart.com", link.get('href', '#')),
                    "title": title_text.strip()[:70], "image": fk_img}

    except Exception as e:
        print(f"  [Flipkart] Error: {e}")
    return None

def scrape_reliance(query):
    safe_q = urllib.parse.quote(query)
    url = f"https://www.reliancedigital.in/ext/raven-api/catalog/v1.0/products?q={safe_q}&page_no=1&page_size=24"
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, */*",
        "Accept-Language": "en-IN,en;q=0.9",
        "Referer": "https://www.reliancedigital.in/",
    }
    try:
        r = requests.get(url, headers=headers, timeout=12)
        r.encoding = 'utf-8'
        print(f"  [Reliance] API status: {r.status_code}")
        if r.status_code != 200:
            return None
        data = r.json()
        items = data.get('items', []) if isinstance(data, dict) else []
        print(f"  [Reliance] {len(items)} items returned")

        for item in items:
            name = item.get('name', '')
            slug = item.get('slug', '')
            price_data = item.get('price', {})
            price = price_data.get('effective', {}).get('min', 0) if isinstance(price_data, dict) else 0
            if not name or not price or price <= 0:
                continue
            print(f"  [Reliance] Candidate: ₹{price:,} | {name}")
            if not is_correct_product(query, name):
                continue

            rd_img = ''
            medias = item.get('medias', [])
            if medias and isinstance(medias, list):
                first = medias[0] if medias else None
                if first and isinstance(first, dict):
                    rd_img = first.get('url') or first.get('image') or ''
                    if rd_img and not rd_img.startswith('http'):
                        rd_img = 'https://www.reliancedigital.in' + rd_img

            print(f"  [Reliance] ✅ {name[:50]} @ ₹{price:,}")
            return {"price": float(price), "display_price": f"₹{price:,}",
                    "rating": "N/A",
                    "link": f"https://www.reliancedigital.in/product/{slug}" if slug else f"https://www.reliancedigital.in/search?q={safe_q}",
                    "title": name[:70], "image": rd_img}

    except Exception as e:
        print(f"  [Reliance] Error: {e}")
    return None

def scrape_vijaysales(query):
    safe_q = urllib.parse.quote(query)
    search_url = f"https://www.vijaysales.com/search?q={safe_q}"
    try:
        resp = _cffi_get(search_url, extra_headers={"Referer": "https://www.vijaysales.com/"})
        if not resp:
            return None
        print(f"  [VijayS] Status: {resp.status_code}, len={len(resp.text)}")
        soup = BeautifulSoup(resp.text, 'lxml')
        cards = soup.select('a.productcollection__item')
        print(f"  [VijayS] Cards found: {len(cards)}")
        for card in cards:
            price_el = card.select_one('div.price span')
            if not price_el:
                price_el = card.find('span', string=re.compile(r'₹'))
            if not price_el:
                continue
            price = clean_price(price_el.get_text(strip=True))
            if price == float('inf'):
                continue
            title = ""
            for sel in ['div.name', 'p.name', 'div.product-name', 'p.product-name',
                        'div.title', 'p.title', 'h2', 'h3']:
                el = card.select_one(sel)
                if el:
                    t = el.get_text(strip=True)
                    if len(t) > 5 and '₹' not in t:
                        title = t
                        break
            if not title:
                img_el = card.select_one('img')
                if img_el:
                    title = img_el.get('alt', '').strip()
            if not title:
                for child in card.children:
                    if hasattr(child, 'get_text'):
                        t = child.get_text(strip=True)
                        if len(t) > 5 and '₹' not in t:
                            title = t
                            break
            if not title or len(title) < 5:
                print(f"  [VijayS] No title for card @ ₹{price:,.0f}")
                continue
            print(f"  [VijayS] Candidate: ₹{price:,.0f} | {title[:60]}")
            if not is_correct_product(query, title):
                continue
            href = card.get('href', '')
            link = urljoin("https://www.vijaysales.com", href) if href else search_url
            img = ""
            img_el = card.select_one('img')
            if img_el:
                img = (img_el.get('data-original') or img_el.get('data-src') or
                       img_el.get('src') or '')
            print(f"  [VijayS] ✅ {title[:50]} @ ₹{price:,.0f}")
            return {"price": price, "display_price": f"₹{price:,.0f}",
                    "rating": "N/A", "link": link, "title": title[:70], "image": img}
        if not cards:
            print("  [VijayS] No cards found")
    except Exception as e:
        print(f"  [VijayS] Error: {e}")
    return None

def scrape_snapdeal(query):
    safe_q = urllib.parse.quote(query)
    url = f"https://www.snapdeal.com/search?keyword={safe_q}"
    try:
        resp = _cffi_get(url)
        if not resp:
            return None
        soup = BeautifulSoup(resp.text, "lxml")
        cards = soup.select("div.product-tuple-listing")
        for card in cards:
            title = card.select_one("p.product-title")
            price = card.select_one("span.product-price")
            if not title or not price:
                continue
            title_text = title.get_text(strip=True)
            price_val = clean_price(price.get_text())
            if not title_text or price_val == float('inf'):
                continue
            print(f"  [Snapdeal] Candidate: ₹{price_val:,.0f} | {title_text[:60]}")
            if not is_correct_product(query, title_text):
                continue
            link = card.select_one("a.dp-widget-link")
            img = card.select_one("img.product-image")
            return {"price": price_val, "display_price": f"₹{price_val:,.0f}",
                    "rating": "N/A", "link": link["href"] if link else url,
                    "title": title_text[:70], "image": img.get("src", "") if img else ""}
    except Exception as e:
        print(f"  [Snapdeal] Error: {e}")
    return None

def scrape_shopclues(query):
    safe_q = urllib.parse.quote(query)
    url = f"https://www.shopclues.com/search?q={safe_q}"
    try:
        resp = _cffi_get(url)
        if not resp:
            return None
        soup = BeautifulSoup(resp.text, "lxml")
        cards = soup.select("div.column, div.search_blocks")
        for card in cards:
            title = card.select_one("h2")
            price = card.select_one(".p_price")
            if not title or not price:
                continue
            title_text = title.get_text(strip=True)
            price_val = clean_price(price.get_text())
            if not title_text or price_val == float('inf'):
                continue
            print(f"  [ShopClues] Candidate: ₹{price_val:,.0f} | {title_text[:60]}")
            if not is_correct_product(query, title_text):
                continue
            link = card.select_one("a")
            img = card.select_one("img")
            return {"price": price_val, "display_price": f"₹{price_val:,.0f}",
                    "rating": "N/A", "link": link["href"] if link else url,
                    "title": title_text[:70], "image": img.get("src", "") if img else ""}
    except Exception as e:
        print(f"  [ShopClues] Error: {e}")
    return None

# ─────────────────────────────────────────────
# ORCHESTRATOR – all stores, best price only from Amazon, Flipkart, Reliance
# ─────────────────────────────────────────────

def get_product_data(query):
    import time
    t0 = time.time()
    safe_q = urllib.parse.quote(query)

    stores_to_scrape = {
        "Amazon": scrape_amazon,
        "Flipkart": scrape_flipkart,
        "Reliance Digital": scrape_reliance,
        "Vijay Sales": scrape_vijaysales,
        "Snapdeal": scrape_snapdeal,
        "ShopClues": scrape_shopclues,
    }

    results = {}
    for store in stores_to_scrape.keys():
        results[store] = {
            "price": float('inf'),
            "display_price": "Not Found",
            "rating": "N/A",
            "link": f"https://www.{store.lower().replace(' ', '')}.com/search?q={safe_q}",
            "title": "No match",
            "image": ""
        }

    print(f"\n[Searching] {query}")

    with ThreadPoolExecutor(max_workers=len(stores_to_scrape)) as executor:
        futures = {executor.submit(scraper, query): store for store, scraper in stores_to_scrape.items()}
        for future in as_completed(futures):
            store = futures[future]
            try:
                result = future.result(timeout=30)
                if result and isinstance(result, dict) and result.get('price', 0) > 0:
                    results[store] = result
                else:
                    results[store]['price'] = float('inf')
            except Exception as e:
                print(f"  [{store}] Error: {e}")
                results[store]['price'] = float('inf')
            print(f"  [{store}] done in {time.time()-t0:.1f}s")

    # Best price only from Amazon, Flipkart, Reliance Digital
    best_stores_set = {"Amazon", "Flipkart", "Reliance Digital"}
    best_store, lowest_price = None, float('inf')
    for store in best_stores_set:
        if store in results:
            price = results[store].get('price', float('inf'))
            if price < lowest_price:
                lowest_price = price
                best_store = store

    best_data = None
    if best_store and lowest_price != float('inf'):
        best_data = {"store": best_store, "display_price": f"₹{lowest_price:,.0f}"}

    print(f"  [Total] {time.time()-t0:.1f}s")
    return {"stores": results, "best": best_data}

if __name__ == "__main__":
    result = get_product_data("iphone 13")
    print(json.dumps(result, indent=2))
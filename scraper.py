import requests
from bs4 import BeautifulSoup
import re
import json
import urllib.parse
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
import random
import os

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def clean_price(price_str):
    if not price_str:
        return float('inf')
    cleaned = re.sub(r'[^\d.]', '', str(price_str))
    try:
        val = float(cleaned)
        return val if val > 0 else float('inf')
    except Exception:
        return float('inf')


def is_correct_product(query, title):
    q_lower = query.lower()
    t_lower = title.lower()

    accessories = ['case', 'cover', 'glass', 'protector', 'cable', 'charger',
                   'adapter', 'skin', 'stand', 'mount', 'holder', 'pouch',
                   'sleeve', 'bumper', 'shield', 'wrap', 'film', 'foil', 'housing']
    if not any(a in q_lower for a in accessories):
        if any(a in t_lower for a in accessories):
            return False

    key_brands = ['iphone', 'samsung', 'oneplus', 'pixel', 'realme', 'redmi',
                  'xiaomi', 'oppo', 'vivo', 'motorola', 'nokia', 'sony', 'asus', 'nothing']
    for brand in key_brands:
        if brand in q_lower and brand not in t_lower:
            return False

    iphone_q = re.search(r'iphone\s*(\d+\s*(?:pro\s*max|pro|plus|max|mini|ultra|e|air)?)', q_lower)
    iphone_t = re.search(r'iphone\s*(\d+\s*(?:pro\s*max|pro|plus|max|mini|ultra|e|air)?)', t_lower)
    if iphone_q:
        q_model = re.sub(r'\s+', '', iphone_q.group(1))
        t_model = re.sub(r'\s+', '', iphone_t.group(1)) if iphone_t else ''
        if q_model != t_model:
            return False

    q_flat = re.sub(r'(\d+)\s*(gb|tb)', lambda m: m.group(1) + m.group(2), q_lower).replace(" ", "")
    t_flat = re.sub(r'(\d+)\s*(gb|tb)', lambda m: m.group(1) + m.group(2), t_lower).replace(" ", "")
    storages = ['64gb', '128gb', '256gb', '512gb', '1tb', '2tb']
    query_has_storage = any(s in q_flat for s in storages)
    for storage in storages:
        if storage in q_flat and storage not in t_flat:
            return False
        if storage in t_flat and storage not in q_flat and query_has_storage:
            return False

    return True


def _cffi_get(url, timeout=14, extra_headers=None):
    from curl_cffi import requests as cffi_requests
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
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
# AMAZON
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
            if card.get('data-component-type') == 'sp-sponsored-result':
                return True
            if card.find('span', string=re.compile(r'^Sponsored$', re.I)):
                return True
            return False

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


# ─────────────────────────────────────────────
# FLIPKART
# ─────────────────────────────────────────────

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


# ─────────────────────────────────────────────
# RELIANCE DIGITAL
# ─────────────────────────────────────────────

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
        items = r.json().get('items', [])
        print(f"  [Reliance] {len(items)} items returned")

        for item in items:
            name  = item.get('name', '')
            slug  = item.get('slug', '')
            price = item.get('price', {}).get('effective', {}).get('min', 0)
            if not name or not price or price <= 0:
                continue
            print(f"  [Reliance] Candidate: ₹{price:,} | {name}")
            if not is_correct_product(query, name):
                continue

            rd_img = ''
            medias = item.get('medias', [])
            if medias and isinstance(medias, list):
                first = medias[0]
                rd_img = first.get('url') or first.get('image') or ''
                if rd_img and not rd_img.startswith('http'):
                    rd_img = 'https://www.reliancedigital.in' + rd_img

            print(f"  [Reliance] ✅ {name[:50]} @ ₹{price:,}")
            return {"price": float(price), "display_price": f"₹{price:,}",
                    "rating": "N/A",
                    "link": f"https://www.reliancedigital.in/product/{slug}",
                    "title": name[:70], "image": rd_img}

    except Exception as e:
        print(f"  [Reliance] Error: {e}")
    return None


# ─────────────────────────────────────────────
# TATA CLIQ
# Site is a pure CSR React shell (13KB, no product data in HTML).

# ─────────────────────────────────────────────
# CROMA
# Uses Croma's public Algolia-backed JSON API
# ─────────────────────────────────────────────

def scrape_croma(query):
    safe_q = urllib.parse.quote_plus(query)
    api_url = (
        f"https://api.croma.com/searchservices/v1/category/search"
        f"?q={safe_q}&currentPage=0&pageSize=10&sortBy=relevance"
    )
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json",
        "Referer": "https://www.croma.com/",
        "Origin": "https://www.croma.com",
    }
    try:
        # Try JSON API first (fast, no browser needed)
        r = requests.get(api_url, headers=headers, timeout=10)
        print(f"  [Croma] API status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            items = (data.get("searchresult", {}).get("products") or
                     data.get("products") or data.get("results") or [])
            print(f"  [Croma] {len(items)} items from API")
            for item in items:
                name = (item.get("name") or item.get("productName") or
                        item.get("title") or "")
                if not name or len(name) < 5:
                    continue
                price = clean_price(
                    item.get("prices", {}).get("sellingPrice") or
                    item.get("price", {}).get("value") or
                    item.get("offerPrice") or item.get("sellingPrice") or
                    item.get("mrp")
                )
                if price == float("inf"):
                    continue
                print(f"  [Croma] Candidate: ₹{price:,.0f} | {name[:60]}")
                if not is_correct_product(query, name):
                    continue
                slug = item.get("url") or item.get("slug") or ""
                link = (f"https://www.croma.com{slug}" if slug.startswith("/")
                        else f"https://www.croma.com/searchB?q={safe_q}")
                rating = str(item.get("rating") or item.get("averageRating") or "N/A")
                images = item.get("images") or []
                img = ""
                if images and isinstance(images, list):
                    first = images[0]
                    img = (first.get("url") or first.get("imageURL") or
                           (first if isinstance(first, str) else ""))
                if not img:
                    img = item.get("thumbnail") or item.get("image") or ""
                print(f"  [Croma] ✅ {name[:50]} @ ₹{price:,.0f} ⭐{rating}")
                return {"price": price, "display_price": f"₹{price:,.0f}",
                        "rating": rating, "link": link, "title": name[:70], "image": img}

        # Fallback: HTML scrape with curl_cffi
        print("  [Croma] API failed, trying HTML...")
        from curl_cffi import requests as cffi_requests
        page_url = f"https://www.croma.com/searchB?q={safe_q}%3Arelevance&text={safe_q}"
        r2 = cffi_requests.get(page_url, impersonate="chrome124", timeout=12)
        print(f"  [Croma] HTML status: {r2.status_code}")
        if r2.status_code == 200:
            soup = BeautifulSoup(r2.text, "lxml")
            for sel in ["li.product-item", "div[class*='product-item']",
                        "div[class*='ProductCard']", "div[class*='plp-grid']"]:
                items_html = [c for c in soup.select(sel)
                              if re.search(r"₹\s*\d", c.get_text())]
                if not items_html:
                    continue
                print(f"  [Croma] HTML selector '{sel}': {len(items_html)} items")
                for item in items_html:
                    name = ""
                    for t_sel in ["h3", "h4", "h2", "[class*='title']", "[class*='name']"]:
                        el = item.select_one(t_sel)
                        if el and len(el.get_text(strip=True)) > 5:
                            name = el.get_text(strip=True)
                            break
                    price_m = re.search(r"₹\s*([\d,]+)", item.get_text())
                    price = clean_price(price_m.group(1)) if price_m else float("inf")
                    if not name or price == float("inf"):
                        continue
                    print(f"  [Croma] HTML Candidate: ₹{price:,.0f} | {name[:60]}")
                    if not is_correct_product(query, name):
                        continue
                    a = item.select_one("a[href]")
                    link = urljoin("https://www.croma.com", a["href"]) if a else page_url
                    img_el = item.select_one("img")
                    img = (img_el.get("src") or img_el.get("data-src", "")) if img_el else ""
                    print(f"  [Croma] ✅ {name[:50]} @ ₹{price:,.0f}")
                    return {"price": price, "display_price": f"₹{price:,.0f}",
                            "rating": "N/A", "link": link, "title": name[:70], "image": img}
                break
        print("  [Croma] No products found")
    except Exception as e:
        print(f"  [Croma] Error: {e}")
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
            # ── Price ──────────────────────────────────────────────────────
            price_el = card.select_one('div.price span')
            if not price_el:
                price_el = card.find('span', string=re.compile(r'₹'))
            if not price_el:
                continue
            price = clean_price(price_el.get_text(strip=True))
            if price == float('inf'):
                continue

            # ── Title ──────────────────────────────────────────────────────
            title = ""
            # Try named divs/paragraphs first
            for sel in ['div.name', 'p.name', 'div.product-name', 'p.product-name',
                        'div.title', 'p.title', 'h2', 'h3']:
                el = card.select_one(sel)
                if el:
                    t = el.get_text(strip=True)
                    if len(t) > 5 and '₹' not in t:
                        title = t
                        break

            # Fall back to img alt text
            if not title:
                img_el = card.select_one('img')
                if img_el:
                    title = img_el.get('alt', '').strip()

            # Last resort: first non-price text block among direct children
            if not title:
                for child in card.children:
                    if hasattr(child, 'get_text'):
                        t = child.get_text(strip=True)
                        if len(t) > 5 and '₹' not in t:
                            title = t
                            break

            if not title or len(title) < 5:
                # Show card HTML once to diagnose further if needed
                print(f"  [VijayS] No title found for card @ ₹{price:,.0f} — HTML: {str(card)[:250]}")
                continue

            print(f"  [VijayS] Candidate: ₹{price:,.0f} | {title[:60]}")
            if not is_correct_product(query, title):
                continue

            # ── Link ───────────────────────────────────────────────────────
            href = card.get('href', '')
            link = urljoin("https://www.vijaysales.com", href) if href else search_url

            # ── Image ──────────────────────────────────────────────────────
            img = ""
            img_el = card.select_one('img')
            if img_el:
                img = (img_el.get('data-original') or img_el.get('data-src') or
                       img_el.get('src') or '')

            print(f"  [VijayS] ✅ {title[:50]} @ ₹{price:,.0f}")
            return {"price": price, "display_price": f"₹{price:,.0f}",
                    "rating": "N/A", "link": link, "title": title[:70], "image": img}

        if not cards:
            print("  [VijayS] Selector a.productcollection__item found nothing")
            print(f"  [VijayS] Page head: {resp.text[:400]}")

    except Exception as e:
        print(f"  [VijayS] Error: {e}")
    return None


# ─────────────────────────────────────────────
# ORCHESTRATOR
# ─────────────────────────────────────────────

def get_product_data(query):
    import time
    t0 = time.time()
    safe_q = urllib.parse.quote(query)
    results = {
        "Amazon":           {"price": float('inf'), "display_price": "Not Found", "rating": "N/A",
                             "link": f"https://www.amazon.in/s?k={safe_q}",
                             "title": "No exact match found", "image": ""},
        "Flipkart":         {"price": float('inf'), "display_price": "Not Found", "rating": "N/A",
                             "link": f"https://www.flipkart.com/search?q={safe_q}",
                             "title": "No exact match found", "image": ""},
        "Reliance Digital": {"price": float('inf'), "display_price": "Not Found", "rating": "N/A",
                             "link": f"https://www.reliancedigital.in/search?q={safe_q}",
                             "title": "No exact match found", "image": ""},
        "Croma":            {"price": float('inf'), "display_price": "Not Found", "rating": "N/A",
                             "link": f"https://www.croma.com/searchB?q={safe_q}%3Arelevance",
                             "title": "No exact match found", "image": ""},
        "Vijay Sales":      {"price": float('inf'), "display_price": "Not Found", "rating": "N/A",
                             "link": f"https://www.vijaysales.com/search?q={safe_q}",
                             "title": "No exact match found", "image": ""},
    }

    print(f"\n[Searching] {query}")

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(scrape_amazon, query):     "Amazon",
            executor.submit(scrape_flipkart, query):   "Flipkart",
            executor.submit(scrape_reliance, query):   "Reliance Digital",
            executor.submit(scrape_croma, query):      "Croma",
            executor.submit(scrape_vijaysales, query): "Vijay Sales",
        }
        for future in as_completed(futures):
            store = futures[future]
            try:
                result = future.result()
            except Exception as e:
                print(f"  [{store}] Unhandled exception: {e}")
                result = None
            if result:
                results[store] = result
            print(f"  [{store}] done in {time.time()-t0:.1f}s")

    best_store, lowest_price = None, float('inf')
    for store, data in results.items():
        if data['price'] < lowest_price:
            lowest_price = data['price']
            best_store = store

    best_data = None
    if best_store and lowest_price != float('inf'):
        best_data = {"store": best_store, "display_price": f"₹{lowest_price:,.0f}"}

    print(f"  [Total] {time.time()-t0:.1f}s")
    return {"stores": results, "best": best_data}
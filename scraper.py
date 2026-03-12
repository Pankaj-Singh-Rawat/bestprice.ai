import requests
from bs4 import BeautifulSoup
import re
import urllib.parse
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor
import random

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

def get_headers(referer=None):
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        **({"Referer": referer} if referer else {}),
    }

def clean_price(price_str):
    if not price_str: return float('inf')
    cleaned = re.sub(r'[^\d.]', '', str(price_str))
    try:
        return float(cleaned)
    except:
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


def scrape_amazon(query):
    safe_q = urllib.parse.quote(query)
    url = f"https://www.amazon.in/s?k={safe_q}"
    session = requests.Session()
    try:
        session.get("https://www.amazon.in", headers=get_headers(), timeout=8)
        r = session.get(url, headers=get_headers(referer="https://www.amazon.in"), timeout=12)
        print(f"  [Amazon] Status: {r.status_code}")
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, 'lxml')
        cards = soup.find_all('div', {'data-component-type': 's-search-result'})
        print(f"  [Amazon] Cards found: {len(cards)}")

        for card in cards:
            if card.find('span', string=re.compile(r'^Sponsored$', re.I)):
                continue
            if card.get('data-component-type') == 'sp-sponsored-result':
                continue

            title_elem = card.select_one('h2 span')
            if not title_elem:
                continue
            title_text = title_elem.get_text(strip=True)
            if len(title_text) < 5:
                continue

            price = float('inf')
            pe = card.select_one('.a-price-whole')
            if pe:
                price = clean_price(pe.get_text(strip=True))
            if price <= 0 or price == float('inf'):
                os_e = card.select_one('.a-price .a-offscreen')
                if os_e:
                    price = clean_price(os_e.get_text(strip=True))
            if price <= 0 or price == float('inf'):
                m = re.search(r'₹\s*([\d,]+)', card.get_text())
                if m:
                    price = clean_price(m.group(1))
            if price <= 0 or price == float('inf'):
                continue

            print(f"  [Amazon] Candidate: ₹{price:,.0f} | {title_text[:60]}")
            if not is_correct_product(query, title_text):
                continue

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
            print(f"  [Amazon] ✅ {title_text[:50]} @ ₹{price:,.0f} ⭐{rating_val}")
            return {"price": price, "display_price": f"₹{price:,.0f}",
                    "rating": rating_val, "link": link, "title": title_text[:70]}

    except Exception as e:
        print(f"  [Amazon] Error: {e}")
    return None


def scrape_flipkart(query):
    safe_q = urllib.parse.quote(query)
    url = f"https://www.flipkart.com/search?q={safe_q}"
    try:
        from curl_cffi import requests as cffi_requests
        resp = cffi_requests.get(url, impersonate="chrome124", timeout=12)
        print(f"  [Flipkart] Status: {resp.status_code}")
        if resp.status_code != 200:
            return None
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
            if price <= 0:
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

            print(f"  [Flipkart] ✅ {title_text[:50]} @ ₹{price:,.0f} ⭐{rating_val}")
            return {"price": price, "display_price": f"₹{price:,.0f}", "rating": rating_val,
                    "link": urljoin("https://www.flipkart.com", link.get('href', '#')),
                    "title": title_text.strip()[:70]}

    except Exception as e:
        print(f"  [Flipkart] Error: {e}")
    return None


def scrape_reliance(query):
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, */*",
        "Accept-Language": "en-IN,en;q=0.9",
        "Referer": "https://www.reliancedigital.in/",
    }
    safe_q = urllib.parse.quote(query)
    url = f"https://www.reliancedigital.in/ext/raven-api/catalog/v1.0/products?q={safe_q}&page_no=1&page_size=24"
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
            if is_correct_product(query, name):
                rating_val = "N/A"
                try:
                    uid = item.get('uid')
                    if uid:
                        rr = requests.get(
                            f"https://www.reliancedigital.in/ext/raven-api/catalog/v1.0/products/{uid}/ratings",
                            headers=headers, timeout=5
                        )
                        if rr.status_code == 200:
                            rd = rr.json()
                            avg = (rd.get('average_rating') or rd.get('rating') or
                                   rd.get('data', {}).get('average_rating'))
                            if avg:
                                rating_val = str(round(float(avg), 1))
                except Exception:
                    pass
                print(f"  [Reliance] ✅ {name[:50]} @ ₹{price:,} ⭐{rating_val}")
                return {"price": float(price), "display_price": f"₹{price:,}",
                        "rating": rating_val,
                        "link": f"https://www.reliancedigital.in/product/{slug}",
                        "title": name[:70]}
    except Exception as e:
        print(f"  [Reliance] Error: {e}")
    return None


def get_product_data(query):
    safe_q = urllib.parse.quote(query)
    results = {
        "Amazon":           {"price": float('inf'), "display_price": "Not Found", "rating": "N/A", "link": f"https://www.amazon.in/s?k={safe_q}", "title": "No exact match found"},
        "Flipkart":         {"price": float('inf'), "display_price": "Not Found", "rating": "N/A", "link": f"https://www.flipkart.com/search?q={safe_q}", "title": "No exact match found"},
        "Reliance Digital": {"price": float('inf'), "display_price": "Not Found", "rating": "N/A", "link": f"https://www.reliancedigital.in/search?q={safe_q}", "title": "No exact match found"},
    }

    print(f"\n[Searching] {query}")

    # All three run in parallel — pure requests, no browser
    with ThreadPoolExecutor(max_workers=3) as executor:
        f_amazon   = executor.submit(scrape_amazon, query)
        f_flipkart = executor.submit(scrape_flipkart, query)
        f_reliance = executor.submit(scrape_reliance, query)

        amazon_result   = f_amazon.result()
        flipkart_result = f_flipkart.result()
        reliance_result = f_reliance.result()

    if amazon_result:   results["Amazon"]           = amazon_result
    if flipkart_result: results["Flipkart"]         = flipkart_result
    if reliance_result: results["Reliance Digital"] = reliance_result

    best_store, lowest_price = None, float('inf')
    for store, data in results.items():
        if data['price'] < lowest_price:
            lowest_price = data['price']
            best_store = store

    best_data = None
    if best_store and lowest_price != float('inf'):
        best_data = {"store": best_store, "display_price": f"₹{lowest_price:,.0f}"}

    return {"stores": results, "best": best_data}
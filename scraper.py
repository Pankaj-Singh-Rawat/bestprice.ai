from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import requests
import re
import urllib.parse
from urllib.parse import urljoin

def clean_price(price_str):
    if not price_str: return float('inf')
    cleaned = re.sub(r'[^\d.]', '', price_str)
    try:
        return float(cleaned)
    except:
        return float('inf')

def is_correct_product(query, title):
    q_lower = query.lower()
    t_lower = title.lower()

    accessories = ['case', 'cover', 'glass', 'protector', 'cable', 'charger',
                   'adapter', 'skin', 'stand', 'mount', 'holder', 'pouch',
                   'sleeve', 'bumper', 'shield', 'wrap', 'film', 'foil']
    if not any(a in q_lower for a in accessories):
        if any(a in t_lower for a in accessories):
            return False

    key_brands = ['iphone', 'samsung', 'oneplus', 'pixel', 'realme', 'redmi',
                  'xiaomi', 'oppo', 'vivo', 'motorola', 'nokia', 'sony', 'asus', 'nothing']
    for brand in key_brands:
        if brand in q_lower and brand not in t_lower:
            return False

    # Match full iphone model including suffix: "16" != "16e", "16 pro" != "16"
    iphone_q = re.search(r'iphone\s*(\d+\s*(?:pro\s*max|pro|plus|max|mini|ultra|e)?)', q_lower)
    iphone_t = re.search(r'iphone\s*(\d+\s*(?:pro\s*max|pro|plus|max|mini|ultra|e)?)', t_lower)
    if iphone_q:
        q_model = re.sub(r'\s+', '', iphone_q.group(1))  # "16 pro" -> "16pro"
        t_model = re.sub(r'\s+', '', iphone_t.group(1)) if iphone_t else ''
        if q_model != t_model:
            return False

    storage_nums = {'64', '128', '256', '512'}
    q_nums = [n for n in re.findall(r'\b(\d{2,3})\b', q_lower) if n not in storage_nums]
    t_nums = set(re.findall(r'\b(\d{2,3})\b', t_lower))
    for num in q_nums:
        if num not in t_nums:
            return False

    q_flat = q_lower.replace(" ", "")
    t_flat = t_lower.replace(" ", "")
    storages = ['64gb', '128gb', '256gb', '512gb', '1tb', '2tb']
    query_has_storage = any(s in q_flat for s in storages)
    for storage in storages:
        if storage in q_flat and storage not in t_flat:
            return False
        if storage in t_flat and storage not in q_flat and query_has_storage:
            return False

    return True


def scrape_reliance(query):
    """
    Use Reliance Digital's internal product search API directly.
    Confirmed endpoint: /ext/raven-api/catalog/v1.0/products?q={query}
    Response structure:
      items[].name                    -> product title
      items[].price.effective.min     -> selling price (INR integer)
      items[].slug                    -> used to build product URL
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, */*",
        "Accept-Language": "en-IN,en;q=0.9",
        "Referer": "https://www.reliancedigital.in/",
    }
    safe_q = urllib.parse.quote(query)
    url = f"https://www.reliancedigital.in/ext/raven-api/catalog/v1.0/products?q={safe_q}&page_no=1&page_size=24"
    try:
        r = requests.get(url, headers=headers, timeout=12)
        r.encoding = 'utf-8'
        print(f"  API status: {r.status_code}")
        if r.status_code != 200:
            return None
        data = r.json()
        items = data.get('items', [])
        print(f"  API returned {len(items)} items")

        for item in items:
            name  = item.get('name', '')
            slug  = item.get('slug', '')
            price = item.get('price', {}).get('effective', {}).get('min', 0)

            if not name or not price or price <= 1000:
                continue

            print(f"  Candidate: ₹{price:,} | {name}")
            if is_correct_product(query, name):
                link = f"https://www.reliancedigital.in/product/{slug}"
                # Try to get rating from product detail API
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
                            avg = rd.get('average_rating') or rd.get('rating') or rd.get('data', {}).get('average_rating')
                            if avg:
                                rating_val = str(round(float(avg), 1))
                except Exception:
                    pass
                return {
                    "price": float(price),
                    "display_price": f"₹{price:,}",
                    "rating": rating_val,
                    "link": link,
                    "title": name[:70],
                }
    except Exception as e:
        print(f"  Reliance API error: {e}")
    return None


def get_product_data(query):
    results = {
        "Amazon":           {"price": float('inf'), "display_price": "Not Found", "rating": "N/A", "link": "#", "title": "No exact match found"},
        "Flipkart":         {"price": float('inf'), "display_price": "Not Found", "rating": "N/A", "link": "#", "title": "No exact match found"},
        "Reliance Digital": {"price": float('inf'), "display_price": "Not Found", "rating": "N/A", "link": "#", "title": "No exact match found"},
    }

    safe_query = urllib.parse.quote(query)

    print(f"\n[Reliance] Searching: {query}")
    rd = scrape_reliance(query)
    if rd:
        results["Reliance Digital"] = rd
        print(f"  ✅ Found: {rd['title']} @ {rd['display_price']}")
    else:
        print("  ❌ No match on Reliance Digital")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=['--disable-blink-features=AutomationControlled', '--no-sandbox'])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={'width': 1920, 'height': 1080},
            locale='en-IN',
            timezone_id='Asia/Kolkata',
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

        # ── AMAZON ────────────────────────────────────────────────────────────
        try:
            print(f"\n[Amazon] Searching: {query}")
            page.goto(f"https://www.amazon.in/s?k={safe_query}", timeout=25000, wait_until='domcontentloaded')
            page.wait_for_timeout(3500)
            page.mouse.wheel(0, 500)
            page.wait_for_timeout(800)

            soup = BeautifulSoup(page.content(), 'html.parser')
            cards = soup.find_all('div', {'data-component-type': 's-search-result'})
            print(f"  Cards found: {len(cards)}")

            for card in cards:
                if card.find('span', string=re.compile(r'^Sponsored$', re.I)):
                    continue
                if card.find('div', {'data-component-type': 'sp-sponsored-result'}):
                    continue

                title_elem = card.select_one('h2 span')
                if not title_elem:
                    continue
                title_text = title_elem.get_text(strip=True)
                if len(title_text) < 10 or 'results for' in title_text.lower():
                    continue

                price = float('inf')
                pe = card.select_one('.a-price-whole')
                if pe:
                    price = clean_price(pe.get_text(strip=True))
                if price <= 1000:
                    os_e = card.select_one('.a-price .a-offscreen')
                    if os_e:
                        price = clean_price(os_e.get_text(strip=True))
                if price <= 1000:
                    m = re.search(r'₹\s*([\d,]+)', card.get_text())
                    if m:
                        price = clean_price(m.group(1))
                if price <= 1000 or price == float('inf'):
                    continue

                print(f"  Candidate: ₹{price:,.0f} | {title_text[:60]}")
                if not is_correct_product(query, title_text):
                    continue

                rating_val = "N/A"
                for sel in ['span.a-icon-alt', 'i[class*="a-star"] span']:
                    elem = card.select_one(sel)
                    if elem:
                        m = re.search(r'([1-5]\.\d)', elem.get_text(strip=True))
                        if m:
                            rating_val = m.group(1)
                            break

                link_elem = card.select_one('a[href*="/dp/"]')
                link = urljoin("https://www.amazon.in", link_elem.get('href', '#')) if link_elem else "#"
                results["Amazon"] = {"price": price, "display_price": f"₹{price:,.0f}", "rating": rating_val, "link": link, "title": title_text[:70]}
                print(f"  ✅ Found: {title_text[:60]} @ ₹{price:,.0f} | ⭐{rating_val}")
                break

        except Exception as e:
            print(f"  ❌ Amazon error: {e}")

        # ── FLIPKART ──────────────────────────────────────────────────────────
        try:
            print(f"\n[Flipkart] Searching: {query}")
            page.goto(f"https://www.flipkart.com/search?q={safe_query}", timeout=25000, wait_until='domcontentloaded')
            page.wait_for_timeout(3000)
            soup = BeautifulSoup(page.content(), 'html.parser')
            product_links = soup.select('a[href*="/p/"]')
            print(f"  Product links found: {len(product_links)}")

            for link in product_links:
                text_content = link.get_text(' ', strip=True)
                if '₹' not in text_content:
                    continue
                price_match = re.search(r'₹\s*([0-9,]+)', text_content)
                if not price_match:
                    continue
                price = clean_price(price_match.group(1))
                if price <= 1000:
                    continue

                title_text = ""
                img = link.select_one('img')
                if img and len(img.get('alt', '')) > 10:
                    title_text = img.get('alt')
                if not title_text:
                    for d in link.find_all('div'):
                        t = d.get_text(strip=True)
                        if len(t) > 20 and '₹' not in t and not re.match(r'^[1-5]\.[0-9]', t):
                            title_text = t
                            break
                if not title_text:
                    continue

                print(f"  Candidate: ₹{price:,.0f} | {title_text[:60]}")
                if not is_correct_product(query, title_text):
                    continue

                rating_val = "N/A"
                for div in link.find_all('div'):
                    if re.match(r'^[1-5]\.[0-9]$', div.get_text(strip=True)):
                        rating_val = div.get_text(strip=True)
                        break

                results["Flipkart"] = {"price": price, "display_price": f"₹{price:,.0f}", "rating": rating_val,
                                       "link": urljoin("https://www.flipkart.com", link.get('href', '#')), "title": title_text.strip()[:70]}
                print(f"  ✅ Found: {title_text[:60]} @ ₹{price:,.0f} | ⭐{rating_val}")
                break

        except Exception as e:
            print(f"  ❌ Flipkart error: {e}")

        browser.close()

    best_store, lowest_price = None, float('inf')
    for store, data in results.items():
        if data['price'] < lowest_price:
            lowest_price = data['price']
            best_store = store

    best_data = None
    if best_store and lowest_price != float('inf'):
        best_data = {"store": best_store, "display_price": f"₹{lowest_price:,.0f}"}

    return {"stores": results, "best": best_data}
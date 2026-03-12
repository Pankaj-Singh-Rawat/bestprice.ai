import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import requests
import re
import urllib.parse
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor

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

    iphone_q = re.search(r'iphone\s*(\d+\s*(?:pro\s*max|pro|plus|max|mini|ultra|e|air)?)', q_lower)
    iphone_t = re.search(r'iphone\s*(\d+\s*(?:pro\s*max|pro|plus|max|mini|ultra|e|air)?)', t_lower)
    if iphone_q:
        q_model = re.sub(r'\s+', '', iphone_q.group(1))
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
    """Pure requests — no browser needed."""
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
        print(f"  [Reliance] API error: {e}")
    return None


def _parse_amazon(html, query):
    try:
        soup = BeautifulSoup(html, 'html.parser')
        cards = soup.find_all('div', {'data-component-type': 's-search-result'})
        print(f"  [Amazon] Cards found: {len(cards)}")
        for card in cards:
            if card.find('span', string=re.compile(r'^Sponsored$', re.I)):
                continue
            if card.find('div', {'data-component-type': 'sp-sponsored-result'}):
                continue
            title_elem = card.select_one('h2 span')
            if not title_elem:
                continue
            title_text = title_elem.get_text(strip=True)
            if len(title_text) < 5 or 'results for' in title_text.lower():
                continue

            price = float('inf')
            pe = card.select_one('.a-price-whole')
            if pe:
                price = clean_price(pe.get_text(strip=True))
            if price <= 0:
                os_e = card.select_one('.a-price .a-offscreen')
                if os_e:
                    price = clean_price(os_e.get_text(strip=True))
            if price <= 0:
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
                    m = re.search(r'([1-5]\.\d)', elem.get_text(strip=True))
                    if m:
                        rating_val = m.group(1)
                        break

            link_elem = card.select_one('a[href*="/dp/"]')
            link = urljoin("https://www.amazon.in", link_elem.get('href', '#')) if link_elem else "#"
            print(f"  [Amazon] ✅ {title_text[:50]} @ ₹{price:,.0f} ⭐{rating_val}")
            return {"price": price, "display_price": f"₹{price:,.0f}",
                    "rating": rating_val, "link": link, "title": title_text[:70]}
    except Exception as e:
        print(f"  [Amazon] Parse error: {e}")
    return None


def _parse_flipkart(html, query):
    try:
        soup = BeautifulSoup(html, 'html.parser')
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
        print(f"  [Flipkart] Parse error: {e}")
    return None


async def _async_scrape(query):
    """Fetch Amazon + Flipkart concurrently using async Playwright."""
    safe_query = urllib.parse.quote(query)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox',
                  '--disable-dev-shm-usage', '--disable-gpu']
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 800},
            locale='en-IN',
            timezone_id='Asia/Kolkata',
        )
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        async def fetch_amazon():
            page = await context.new_page()
            try:
                print("  [Amazon] Navigating...")
                await page.goto(f"https://www.amazon.in/s?k={safe_query}",
                                timeout=20000, wait_until='domcontentloaded')
                try:
                    await page.wait_for_selector('div[data-component-type="s-search-result"]', timeout=5000)
                except:
                    pass
                await page.mouse.wheel(0, 400)
                await page.wait_for_timeout(300)
                return await page.content()
            except Exception as e:
                print(f"  [Amazon] Fetch error: {e}")
                return ""
            finally:
                await page.close()

        async def fetch_flipkart():
            page = await context.new_page()
            try:
                print("  [Flipkart] Navigating...")
                await page.goto(f"https://www.flipkart.com/search?q={safe_query}",
                                timeout=20000, wait_until='domcontentloaded')
                try:
                    await page.wait_for_selector('a[href*="/p/"]', timeout=5000)
                except:
                    pass
                await page.wait_for_timeout(300)
                return await page.content()
            except Exception as e:
                print(f"  [Flipkart] Fetch error: {e}")
                return ""
            finally:
                await page.close()

        # Both pages load truly in parallel
        amazon_html, flipkart_html = await asyncio.gather(
            fetch_amazon(),
            fetch_flipkart(),
        )

        await browser.close()

    return amazon_html, flipkart_html


def get_product_data(query):
    safe_q = urllib.parse.quote(query)
    results = {
        "Amazon":           {"price": float('inf'), "display_price": "Not Found", "rating": "N/A", "link": f"https://www.amazon.in/s?k={safe_q}", "title": "No exact match found"},
        "Flipkart":         {"price": float('inf'), "display_price": "Not Found", "rating": "N/A", "link": f"https://www.flipkart.com/search?q={safe_q}", "title": "No exact match found"},
        "Reliance Digital": {"price": float('inf'), "display_price": "Not Found", "rating": "N/A", "link": f"https://www.reliancedigital.in/search?q={safe_q}", "title": "No exact match found"},
    }

    print(f"\n[Searching] {query}")

    # Run Reliance (requests) in a thread while async browser fetches Amazon+Flipkart
    with ThreadPoolExecutor(max_workers=1) as executor:
        reliance_future = executor.submit(scrape_reliance, query)

        # Run async browser scraping in the main thread's event loop
        amazon_html, flipkart_html = asyncio.run(_async_scrape(query))

        reliance_result = reliance_future.result()

    # Parse HTML (pure CPU, instant)
    amazon_result   = _parse_amazon(amazon_html, query)
    flipkart_result = _parse_flipkart(flipkart_html, query)

    if amazon_result:
        results["Amazon"] = amazon_result
    if flipkart_result:
        results["Flipkart"] = flipkart_result
    if reliance_result:
        results["Reliance Digital"] = reliance_result

    best_store, lowest_price = None, float('inf')
    for store, data in results.items():
        if data['price'] < lowest_price:
            lowest_price = data['price']
            best_store = store

    best_data = None
    if best_store and lowest_price != float('inf'):
        best_data = {"store": best_store, "display_price": f"₹{lowest_price:,.0f}"}

    return {"stores": results, "best": best_data}
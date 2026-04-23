"""
scraper.py  —  BestPrice.ai
Scrapes: Amazon, Flipkart, Reliance Digital, Vijay Sales, Meesho.
Accessory filter ensures "iphone" doesn't return cases, but "iphone case" does.
"""

from __future__ import annotations

import json
import random
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, wait
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

# ── Pre-compiled regexes ────────────────────────────────────────────────────
RE_PRICE_SYMBOL  = re.compile(r'₹\s*([\d,]+)')
RE_DIGITS        = re.compile(r'\d+')
RE_WORDS         = re.compile(r'\b[a-z0-9]+\b')
RE_RATING_INLINE = re.compile(r'([1-5]\.\d)')
RE_RATING_DIV    = re.compile(r'^[1-5]\.[0-9]$')
RE_SPONSORED     = re.compile(r'^Sponsored$', re.I)
RE_LEADING_NUM   = re.compile(r'^\d+\.\s*')
RE_JUNK_PRICE    = re.compile(r'[₹,\s]')

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]
BROWSERS = ["chrome124", "chrome123", "chrome120", "chrome116"]

ACCESSORY_WORDS = frozenset([
    'case', 'cases', 'cover', 'covers', 'protector', 'protectors', 'cable', 'cables',
    'charger', 'chargers', 'adapter', 'adapters', 'skin', 'skins', 'stand', 'stands',
    'mount', 'mounts', 'holder', 'holders', 'pouch', 'pouches', 'sleeve', 'sleeves',
    'bumper', 'bumpers', 'shield', 'shields', 'wrap', 'wraps', 'film', 'films',
    'foil', 'foils', 'sticker', 'stickers', 'decal', 'decals', 'tempered glass',
    'screen guard', 'installation kit', 'installation service', 'toy model', 'miniature',
])
_ACC_PATTERNS = [re.compile(r'\b' + re.escape(a) + r'\b', re.I) for a in ACCESSORY_WORDS]

KEY_BRANDS = frozenset([
    'apple', 'iphone', 'macbook', 'ipad',
    'samsung', 'oneplus', 'pixel', 'realme',
    'redmi', 'xiaomi', 'oppo', 'vivo', 'motorola',
    'nokia', 'sony', 'asus', 'nothing', 'lenovo', 'hp', 'dell',
    'lg', 'voltas', 'whirlpool', 'godrej', 'bajaj', 'havells',
    'bosch', 'prestige', 'philips', 'panasonic',
])

STOP_WORDS = frozenset([
    'the', 'and', 'for', 'with', 'from', 'that', 'this', 'are',
    'was', 'has', 'not', 'but', 'all', 'any',
])

# ── Helpers ─────────────────────────────────────────────────────────────────
def clean_price(price_str) -> float:
    if not price_str:
        return float('inf')
    s = RE_JUNK_PRICE.sub('', str(price_str).strip()).split('.')[0]
    s = re.sub(r'[^\d]', '', s)
    try:
        val = int(s)
        return float(val) if val > 50 else float('inf')
    except (ValueError, OverflowError):
        return float('inf')

def _fmt(price: float) -> str:
    return f"₹{price:,.0f}" if price != float('inf') else "Not Found"

def is_correct_product(query: str, title: str) -> bool:
    if not query or not title:
        return False
    q = query.lower().strip()
    t = RE_LEADING_NUM.sub('', title.lower().strip())
    query_wants_acc = any(p.search(q) for p in _ACC_PATTERNS)
    if not query_wants_acc and any(p.search(t) for p in _ACC_PATTERNS):
        return False
    for brand in KEY_BRANDS:
        if re.search(r'\b' + re.escape(brand) + r'\b', q):
            if not re.search(r'\b' + re.escape(brand) + r'\b', t):
                return False
    q_words = [w for w in RE_WORDS.findall(q) if len(w) > 1 and w not in STOP_WORDS]
    if not q_words:
        return True
    matched = sum(1 for w in q_words if w in t)
    if len(q_words) <= 2:
        return matched >= 1
    return matched / len(q_words) >= 0.35

# ── HTTP helpers ─────────────────────────────────────────────────────────────
_session = requests.Session()
_session.headers.update({
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
})

def _cffi_get(url: str, timeout: int = 12, extra_headers: dict | None = None):
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "User-Agent": random.choice(USER_AGENTS),
        **(extra_headers or {}),
    }
    browsers = random.sample(BROWSERS[:3], 2) + ["chrome116"]
    for browser in browsers:
        try:
            r = cffi_requests.get(url, impersonate=browser, timeout=timeout, headers=headers)
            if r.status_code == 200:
                return r
            if r.status_code in (403, 429, 503):
                return None
        except Exception as e:
            print(f"    [{browser}] {type(e).__name__}: {e}")
    return None

# ── Amazon ─────────────────────────────────────────────────────────────
_RE_AMAZON_TITLE_JUNK = re.compile(r'^(sponsored|ad|advertisement|\d[\d.]* out of \d[\d.]* stars?)', re.I)

def _amazon_title(card) -> str:
    for sel in [
        'h2 a.a-link-normal span.a-text-normal',
        'h2 a.a-link-normal span',
        'span.a-size-medium.a-color-base.a-text-normal',
        'span.a-size-base-plus.a-color-base.a-text-normal',
        '[data-cy="title-recipe"] h2 a span',
        '[data-cy="title-recipe"] span.a-text-normal',
    ]:
        el = card.select_one(sel)
        if el:
            t = el.get_text(strip=True)
            if len(t) > 8 and not _RE_AMAZON_TITLE_JUNK.match(t.strip()):
                return t
    h2 = card.select_one('h2')
    if h2:
        for span in h2.find_all('span'):
            t = span.get_text(strip=True)
            if len(t) > 8 and not _RE_AMAZON_TITLE_JUNK.match(t.strip()):
                return t
    a = card.select_one('a[href*="/dp/"][aria-label]')
    if a:
        t = a.get('aria-label', '').strip()
        if len(t) > 8:
            return t
    return ''

def _amazon_price(card) -> float:
    for sel in ['.a-price .a-offscreen', 'span.a-offscreen']:
        for el in card.select(sel):
            p = clean_price(el.get_text(strip=True))
            if p != float('inf'):
                return p
    pe = card.select_one('.a-price-whole')
    if pe:
        p = clean_price(pe.get_text(strip=True))
        if p != float('inf'):
            return p
    m = RE_PRICE_SYMBOL.search(card.get_text())
    if m:
        return clean_price(m.group(1))
    return float('inf')

def scrape_amazon(query: str) -> dict | None:
    safe_q = urllib.parse.quote(query)
    url = f"https://www.amazon.in/s?k={safe_q}"
    try:
        resp = _cffi_get(url, extra_headers={"Referer": "https://www.amazon.in/"})
        if not resp:
            return None
        if 'captcha' in resp.text.lower() or 'robot check' in resp.text.lower():
            print("  [Amazon] Blocked/CAPTCHA")
            return None
        soup = BeautifulSoup(resp.text, 'lxml')
        all_cards = soup.find_all('div', {'data-component-type': 's-search-result'})
        organic = [c for c in all_cards if not c.find('span', string=RE_SPONSORED)]
        sponsored = [c for c in all_cards if c.find('span', string=RE_SPONSORED)]
        def _parse(card):
            title = _amazon_title(card)
            if not title or not is_correct_product(query, title):
                return None
            price = _amazon_price(card)
            if price == float('inf'):
                return None
            rating = "N/A"
            el = card.select_one('span.a-icon-alt')
            if el:
                m = RE_RATING_INLINE.search(el.get_text())
                if m:
                    rating = m.group(1)
            link_el = card.select_one('a[href*="/dp/"]')
            link = urljoin("https://www.amazon.in", link_el['href']) if link_el else url
            img_el = card.select_one('img.s-image')
            img = img_el.get('src', '') if img_el else ''
            return {"price": price, "display_price": _fmt(price), "rating": rating, "link": link, "title": title[:80], "image": img}
        for card in organic + sponsored:
            r = _parse(card)
            if r:
                print(f"  [Amazon] ✅ {r['title'][:50]} @ {r['display_price']}")
                return r
    except Exception as e:
        print(f"  [Amazon] Error: {e}")
    return None

# ── Flipkart ───────────────────────────────────────────────────────────
def scrape_flipkart(query: str) -> dict | None:
    safe_q = urllib.parse.quote(query)
    url = f"https://www.flipkart.com/search?q={safe_q}"
    try:
        resp = _cffi_get(url)
        if not resp:
            return None
        soup = BeautifulSoup(resp.text, 'lxml')
        for link in soup.select('a[href*="/p/"]'):
            text_content = link.get_text(' ', strip=True)
            if '₹' not in text_content:
                continue
            pm = RE_PRICE_SYMBOL.search(text_content)
            if not pm:
                continue
            price = clean_price(pm.group(1))
            if price == float('inf'):
                continue
            title = ''
            img = link.select_one('img')
            if img and len(img.get('alt', '')) > 5:
                title = img['alt']
            if not title:
                for d in link.find_all('div'):
                    t = RE_LEADING_NUM.sub('', d.get_text(strip=True))
                    if len(t) > 10 and '₹' not in t and not re.match(r'^[1-5]\.[0-9]', t):
                        title = t
                        break
            if not title:
                continue
            title = RE_LEADING_NUM.sub('', title).strip()
            if not is_correct_product(query, title):
                continue
            rating = "N/A"
            for d in link.find_all('div'):
                if RE_RATING_DIV.match(d.get_text(strip=True)):
                    rating = d.get_text(strip=True)
                    break
            fk_img = ''
            if img:
                fk_img = img.get('src') or img.get('data-src') or ''
            print(f"  [Flipkart] ✅ {title[:50]} @ {_fmt(price)}")
            return {"price": price, "display_price": _fmt(price), "rating": rating,
                    "link": urljoin("https://www.flipkart.com", link.get('href', '#')),
                    "title": title[:80], "image": fk_img}
    except Exception as e:
        print(f"  [Flipkart] Error: {e}")
    return None

# ── Reliance Digital ────────────────────────────────────────────────────
def scrape_reliance(query: str) -> dict | None:
    safe_q = urllib.parse.quote(query)
    url = f"https://www.reliancedigital.in/ext/raven-api/catalog/v1.0/products?q={safe_q}&page_no=1&page_size=24"
    try:
        r = _session.get(url, headers={
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json",
            "Accept-Language": "en-IN,en;q=0.9",
            "Referer": "https://www.reliancedigital.in/",
        }, timeout=12)
        if r.status_code != 200:
            return None
        items = r.json().get('items', []) if isinstance(r.json(), dict) else []
        for item in items:
            name = item.get('name', '')
            if not name or not is_correct_product(query, name):
                continue
            price_data = item.get('price', {})
            price = float(price_data.get('effective', {}).get('min', 0) or 0)
            if price <= 0:
                continue
            slug = item.get('slug', '')
            medias = item.get('medias', [])
            img = ''
            if medias and isinstance(medias[0], dict):
                img = medias[0].get('url') or medias[0].get('image') or ''
                if img and not img.startswith('http'):
                    img = 'https://www.reliancedigital.in' + img
            print(f"  [Reliance] ✅ {name[:50]} @ {_fmt(price)}")
            return {"price": price, "display_price": _fmt(price), "rating": "N/A",
                    "link": f"https://www.reliancedigital.in/product/{slug}" if slug else f"https://www.reliancedigital.in/search?q={safe_q}",
                    "title": name[:80], "image": img}
    except Exception as e:
        print(f"  [Reliance] Error: {e}")
    return None

# ── Vijay Sales ─────────────────────────────────────────────────────────
def scrape_vijaysales(query: str) -> dict | None:
    params = {"q": query, "page": 1, "rows": 30}
    url = "https://mdm.vijaysales.com/web/api/search/unbxd-search/v1"
    print(f"  [Vijay Sales] API request")
    try:
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json",
            "Referer": "https://www.vijaysales.com/",
            "Origin": "https://www.vijaysales.com",
        }
        resp = _session.get(url, headers=headers, params=params, timeout=12)
        if resp.status_code != 200:
            print(f"  [Vijay Sales] API returned {resp.status_code}")
            return None
        data = resp.json()
        products = data.get('response', {}).get('products', [])
        if not products:
            print(f"  [Vijay Sales] No products found")
            return None
        for item in products:
            title = item.get('title') or item.get('name')
            if not title or not is_correct_product(query, title):
                continue
            price = float(item.get('price', 0))
            if price <= 0:
                continue
            link = item.get('productUrl')
            if link and not link.startswith('http'):
                link = 'https://www.vijaysales.com' + link
            if not link:
                link = f"https://www.vijaysales.com/search?q={urllib.parse.quote(query)}"
            img_list = item.get('imageUrl', [])
            img = img_list[0] if img_list else ''
            if img and not img.startswith('http'):
                img = 'https://www.vijaysales.com' + img
            print(f"  [Vijay Sales] ✅ {title[:50]} @ {_fmt(price)}")
            return {
                "price": price,
                "display_price": _fmt(price),
                "rating": "N/A",
                "link": link,
                "title": title[:80],
                "image": img,
            }
    except Exception as e:
        print(f"  [Vijay Sales] API error: {e}")
    return None

# ── Meesho strict filter (accessory + word match) ──────────────────────
def _strict_meesho_filter(query: str, title: str) -> bool:
    q_lower = query.lower()
    t_lower = title.lower()
    for pattern in _ACC_PATTERNS:
        if pattern.search(t_lower) and not pattern.search(q_lower):
            return False
    q_words = [w for w in RE_WORDS.findall(q_lower) if len(w) > 2]
    if len(q_words) <= 3:
        found = False
        for w in q_words:
            if re.search(r'\b' + re.escape(w) + r'\b', t_lower):
                found = True
                break
        if not found:
            return False
    return True

# ── Meesho (API based) ──────────────────────────────────────────────────
def scrape_meesho(query: str) -> dict | None:
    url = "https://www.meesho.com/api/v1/products/search"
    print(f"  [Meesho] API request")
    try:
        from curl_cffi import requests as cffi_requests
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Referer": f"https://www.meesho.com/search?q={urllib.parse.quote(query)}&searchType=manual&searchIdentifier=text_search",
            "Origin": "https://www.meesho.com",
            "meesho-iso-country-code": "IN",
        }
        
        payload = {
            "query": query,
            "type": "text_search",
            "page": 1,
            "offset": 0,
            "limit": 20,
            "cursor": None,
            "isDevicePhone": False
        }
        
        resp = cffi_requests.post(
            url,
            headers=headers,
            json=payload,
            impersonate="chrome124",
            timeout=12
        )
        
        if resp.status_code != 200:
            print(f"  [Meesho] API returned HTTP {resp.status_code}")
            return None
        
        data = resp.json()
        products = data.get("catalogs", [])
        if not products:
            print(f"  [Meesho] No products found")
            return None
        
        for item in products[:20]:
            title = item.get("name")
            if not title:
                continue
            if not _strict_meesho_filter(query, title):
                continue
            price = float(item.get("min_catalog_price", 0))
            if price <= 0:
                continue
            if not is_correct_product(query, title):
                continue
            
            slug = item.get("slug")
            product_id = item.get("product_id")
            if slug:
                product_url = f"https://www.meesho.com/{slug}/p/{product_id}" if product_id else f"https://www.meesho.com/{slug}"
            else:
                product_url = f"https://www.meesho.com/search?q={urllib.parse.quote(query)}"
            images = item.get("product_images", [])
            img = images[0].get("url") if images else item.get("image", "")
            if img and not img.startswith("http"):
                img = "https:" + img if img.startswith("//") else img
            rating = str(item.get("catalog_reviews_summary", {}).get("average_rating", "N/A"))
            if rating in ("0", "None"):
                rating = "N/A"
            print(f"  [Meesho] ✅ {title[:50]} @ {_fmt(price)}")
            return {
                "price": price,
                "display_price": _fmt(price),
                "rating": rating,
                "link": product_url,
                "title": title[:80],
                "image": img,
            }
    except Exception as e:
        print(f"  [Meesho] Error: {e}")
    return None

# ── Orchestrator (5 stores) ─────────────────────────────────────────────────
STORES: dict[str, callable] = {
    "Flipkart":         scrape_flipkart,
    "Amazon":           scrape_amazon,
    "Reliance Digital": scrape_reliance,
    "Vijay Sales":      scrape_vijaysales,
    "Meesho":           scrape_meesho,
}
_executor = ThreadPoolExecutor(max_workers=len(STORES))
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 600

def get_product_data(query: str, timeout: float = 18.0) -> dict:
    q = query.strip().lower()
    now = time.time()
    if q in _CACHE:
        ts, cached = _CACHE[q]
        if now - ts < _CACHE_TTL:
            print(f"[Cache] hit for '{q}'")
            return cached
    t0 = time.time()
    safe_q = urllib.parse.quote(query)
    print(f"\n[Searching] {query}")
    results: dict = {
        store: {
            "price": float('inf'),
            "display_price": "Not Found",
            "rating": "N/A",
            "link": f"https://www.{store.lower().replace(' ', '')}.com/search?q={safe_q}",
            "title": "No match",
            "image": "",
        }
        for store in STORES
    }
    future_map = {_executor.submit(fn, query): store for store, fn in STORES.items()}
    done, pending = wait(future_map, timeout=timeout)
    for future in done:
        store = future_map[future]
        try:
            r = future.result()
            if r and isinstance(r, dict) and r.get('price', float('inf')) < float('inf'):
                results[store] = r
        except Exception as e:
            print(f"  [{store}] Exception: {e}")
    for future in pending:
        store = future_map[future]
        print(f"  [{store}] Timed out — skipped")
        future.cancel()

    # Find best deal (no price filter anymore)
    best_store, lowest = None, float('inf')
    for store, data in results.items():
        p = data.get('price', float('inf'))
        if p < lowest:
            lowest, best_store = p, store
    prices_found = [d['price'] for d in results.values() if d['price'] != float('inf')]
    max_price = max(prices_found) if prices_found else float('inf')
    savings = (max_price - lowest) if (max_price != float('inf') and lowest != float('inf') and max_price > lowest) else 0
    best_data = None
    if best_store and lowest != float('inf'):
        best_data = {
            "store": best_store,
            "display_price": _fmt(lowest),
            "savings": int(savings),
            "savings_display": _fmt(savings) if savings > 0 else None,
        }
    out = {"stores": results, "best": best_data}
    _CACHE[q] = (time.time(), out)
    print(f"  [Total] {time.time() - t0:.1f}s")
    return out

if __name__ == "__main__":
    result = get_product_data("samsung galaxy s24")
    print(json.dumps(result, indent=2, default=str))
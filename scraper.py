import requests
from bs4 import BeautifulSoup
import re
import json
import urllib.parse
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, wait
import random
import time
from curl_cffi import requests as cffi_requests

# ─────────────────────────────────────────────
# PERF: Pre-compiled regexes (compile once, reuse)
# ─────────────────────────────────────────────
RE_PRICE_CLEAN   = re.compile(r'[^\d]')          # strip everything except digits (no dot — avoids ₹1,299.00 → 1299.00 → wrong)
RE_PRICE_SYMBOL  = re.compile(r'₹\s*([\d,]+)')
RE_DIGITS        = re.compile(r'\d+')
RE_WORDS         = re.compile(r'\b[a-z0-9]+\b')
RE_RATING_INLINE = re.compile(r'([1-5]\.\d)')
RE_RATING_DIV    = re.compile(r'^[1-5]\.[0-9]$')
RE_SPONSORED     = re.compile(r'^Sponsored$', re.I)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

BROWSERS = ["chrome124", "chrome123", "chrome120", "chrome116"]

# ─────────────────────────────────────────────
# CATEGORY MAPPINGS
# Each entry: canonical_name → (match_keywords_for_query, required_keywords_in_title)
# They can differ — e.g. query "ac" should require "air conditioner" or "ton" in title
# ─────────────────────────────────────────────
GENERIC_CATEGORIES = {
    "phone":            (["phone", "mobile", "smartphone", "cellphone"],
                         ["phone", "mobile", "smartphone", "cellphone"]),
    "tv":               (["tv", "television", "led tv", "lcd tv", "oled", "qled", "ultra hd"],
                         ["tv", "television", "oled", "qled"]),
    "laptop":           (["laptop", "notebook"],
                         ["laptop", "notebook"]),
    "ac":               (["ac", "air conditioner", "aircon", "inverter ac", "split ac", "window ac"],
                         ["air conditioner", "split ac", "window ac", "inverter ac", "ton", "5 star ac", "3 star ac"]),
    "refrigerator":     (["refrigerator", "fridge", "freezer"],
                         ["refrigerator", "fridge", "freezer"]),
    "washing machine":  (["washing machine", "washer", "fully automatic", "semi automatic"],
                         ["washing machine", "washer", "fully automatic", "semi automatic"]),
    "headphones":       (["headphones", "earphones", "earbuds", "neckband"],
                         ["headphone", "earphone", "earbud", "neckband"]),
    "smartwatch":       (["smartwatch", "smart watch", "fitness tracker"],
                         ["smartwatch", "smart watch", "fitness tracker", "fitness band"]),
    "tablet":           (["tablet", "ipad", "android pad"],
                         ["tablet", "ipad", "pad"]),
    "monitor":          (["monitor", "display screen"],
                         ["monitor", "display"]),
    "printer":          (["printer", "inkjet", "laser printer"],
                         ["printer"]),
    "microwave":        (["microwave", "microwave oven"],
                         ["microwave"]),
    "mixer":            (["mixer", "mixer grinder", "juicer mixer"],
                         ["mixer", "juicer", "grinder"]),
    "fan":              (["ceiling fan", "pedestal fan", "table fan", "exhaust fan"],
                         ["fan"]),
    "cooler":           (["air cooler", "desert cooler"],
                         ["cooler"]),
    "geyser":           (["geyser", "water heater"],
                         ["geyser", "water heater"]),
    "iron":             (["steam iron", "dry iron", "garment steamer"],
                         ["iron", "steamer"]),
    "vacuum cleaner":   (["vacuum cleaner", "robot vacuum"],
                         ["vacuum", "cleaner"]),
    "trimmer":          (["trimmer", "hair trimmer", "beard trimmer"],
                         ["trimmer"]),
    "shaver":           (["electric shaver", "electric razor"],
                         ["shaver", "razor"]),
    "kettle":           (["electric kettle"],
                         ["kettle"]),
    "toaster":          (["bread toaster", "pop-up toaster"],
                         ["toaster"]),
    "induction":        (["induction cooktop", "induction stove"],
                         ["induction"]),
    "samsung galaxy":   (["samsung galaxy"],           ["samsung", "galaxy"]),
    "iphone":           (["iphone"],                   ["iphone"]),
    "oneplus":          (["oneplus"],                  ["oneplus"]),
    "pixel":            (["google pixel", "pixel phone"], ["pixel"]),
    "realme":           (["realme"],                   ["realme"]),
    "redmi":            (["redmi", "xiaomi"],          ["redmi", "xiaomi"]),
    "oppo":             (["oppo"],                     ["oppo"]),
    "vivo":             (["vivo"],                     ["vivo"]),
    "motorola":         (["motorola", "moto g", "moto e"], ["motorola", "moto"]),
    "nokia":            (["nokia"],                    ["nokia"]),
    "asus":             (["asus"],                     ["asus"]),
    "nothing":          (["nothing phone"],            ["nothing"]),
}

# PERF: frozenset for O(1) lookups
KEY_BRANDS = frozenset([
    'iphone', 'samsung', 'oneplus', 'pixel', 'realme',
    'redmi', 'xiaomi', 'oppo', 'vivo', 'motorola',
    'nokia', 'sony', 'asus', 'nothing'
])

# Single-word accessories — checked as whole words in title to avoid false positives
# e.g. "case" as a standalone word, not inside "suitcase" or "staircase"
ACCESSORY_WORDS = [
    'case', 'cover', 'protector', 'cable', 'charger', 'adapter',
    'skin', 'stand', 'mount', 'holder', 'pouch', 'sleeve',
    'bumper', 'shield', 'wrap', 'film', 'foil', 'sticker', 'decal',
    'miniature', 'tempered glass', 'back glass', 'screen guard',
    'installation kit', 'installation service', 'toy model',
]
# Compiled as word-boundary patterns for whole-word matching
_ACCESSORY_PATTERNS = [re.compile(r'\b' + re.escape(a) + r'\b') for a in ACCESSORY_WORDS]


def _query_matches_category(q_lower: str) -> tuple[str | None, list[str]]:
    """Return (category_name, required_title_keywords) for the query, or (None, [])."""
    for cat, (q_keywords, t_keywords) in GENERIC_CATEGORIES.items():
        # Exact category name match OR any query keyword present as whole word
        if q_lower == cat:
            return cat, t_keywords
        for kw in q_keywords:
            # Use word-boundary check so "ac" doesn't match "black" or "space"
            if re.search(r'\b' + re.escape(kw) + r'\b', q_lower):
                return cat, t_keywords
    return None, []


def is_generic_category(query: str) -> bool:
    q_lower = query.lower().strip()
    cat, _ = _query_matches_category(q_lower)
    if cat:
        return True
    # Treat short queries with no digits and no known brand as generic
    if len(q_lower.split()) <= 3 and not RE_DIGITS.search(q_lower):
        if not any(re.search(r'\b' + re.escape(b) + r'\b', q_lower) for b in KEY_BRANDS):
            return True
    return False


def is_correct_product(query: str, title: str) -> bool:
    """
    Returns True only if `title` is actually the kind of product `query` is asking for.

    Key fixes vs previous version:
    - Accessories are matched as whole words (no false 'case' inside 'suitcase')
    - Category matching uses word-boundary regex so 'ac' won't match 'black' or 'space'
    - Category title validation uses *required* keywords (not just any common word)
      so "AC sticker" fails because title has no air-conditioner keyword
    - Brand enforcement uses word-boundary regex
    """
    if not query or not title:
        return False
    q_lower = query.lower().strip()
    t_lower = title.lower()

    # ── 1. Accessory filter ──────────────────────────────────────────────────
    # If the query isn't asking for an accessory, reject any title that is one
    query_wants_accessory = any(p.search(q_lower) for p in _ACCESSORY_PATTERNS)
    if not query_wants_accessory:
        if any(p.search(t_lower) for p in _ACCESSORY_PATTERNS):
            print(f"    [Filter] Accessory rejected: {title[:60]}")
            return False

    # ── 2. Brand enforcement ─────────────────────────────────────────────────
    for brand in KEY_BRANDS:
        brand_in_query = re.search(r'\b' + re.escape(brand) + r'\b', q_lower)
        brand_in_title = re.search(r'\b' + re.escape(brand) + r'\b', t_lower)
        if brand_in_query and not brand_in_title:
            print(f"    [Filter] Brand '{brand}' missing from title: {title[:60]}")
            return False

    # ── 3. Category keyword check ────────────────────────────────────────────
    cat, required_title_kws = _query_matches_category(q_lower)
    if cat:
        # Title MUST contain at least one of the required category keywords
        if required_title_kws:
            if not any(re.search(r'\b' + re.escape(kw) + r'\b', t_lower) for kw in required_title_kws):
                print(f"    [Filter] Category '{cat}' keyword missing in title: {title[:60]}")
                return False
        return True

    # ── 4. Specific query (model number / full name) ─────────────────────────
    STOP = frozenset(['the','and','for','with','from','inch','that','this',
                      'are','was','has','not','but','all','any'])
    # Include short tokens IF they are digits (model numbers like "12", "13")
    q_words = [
        w for w in RE_WORDS.findall(q_lower)
        if (len(w) > 2 or (w.isdigit() and len(w) >= 2)) and w not in STOP
    ]
    if not q_words:
        return True

    t_words_set = set(RE_WORDS.findall(t_lower))

    # ── Model-identifier check (FIRST, before overlap) ───────────────────────
    # If query has model tokens (s24, g14) or generation numbers (12, 13, 16),
    # the title MUST contain at least one — prevents "OnePlus Nord CE 4" passing
    # a "oneplus 12" query, and "G16" laptop passing a "g14" query.
    RE_MODEL_TOKEN = re.compile(r'[a-z]+\d|\d+[a-z]')
    model_tokens   = [w for w in q_words if RE_MODEL_TOKEN.search(w)]
    numeric_tokens = [w for w in q_words if w.isdigit()]   # "12", "13", "16" etc.
    all_model_tokens = model_tokens + numeric_tokens
    if all_model_tokens:
        # Alphanumeric tokens (g14, s24): must be a whole word in title
        # Pure numeric tokens (12, 16): substring is fine — "16" inside "PHN16S" is a valid match
        def _token_in_title(tok: str, title_lower: str, title_words: set) -> bool:
            if tok.isdigit():
                return tok in title_lower          # substring OK for bare numbers
            return tok in title_words              # whole-word for mixed tokens
        if not any(_token_in_title(tok, t_lower, t_words_set) for tok in all_model_tokens):
            print(f"    [Filter] Model token missing — query '{query}': {title[:60]}")
            return False

    # ── Word-overlap check ───────────────────────────────────────────────────
    matched = [w for w in q_words if w in t_lower]
    if len(matched) / len(q_words) >= 0.5:
        return True

    # ── Brand + product-type fallback (for Reliance part-number titles) ──────
    PRODUCT_TYPE_WORDS = frozenset([
        'laptop', 'notebook', 'gaming', 'phone', 'mobile', 'smartphone',
        'tablet', 'tv', 'television', 'monitor', 'printer', 'camera',
        'refrigerator', 'fridge', 'washer', 'microwave', 'headphone',
        'earphone', 'smartwatch', 'speaker', 'router', 'projector',
    ])
    q_brand_words  = [w for w in q_words if len(w) >= 4 and not w.isdigit()]
    brand_in_title = any(w in t_words_set for w in q_brand_words)
    type_in_title  = bool(PRODUCT_TYPE_WORDS & t_words_set)
    type_in_query  = bool(PRODUCT_TYPE_WORDS & set(q_words))

    if brand_in_title and (type_in_title or not type_in_query):
        return True

    print(f"    [Filter] Specific query '{query}' – low word overlap: {title[:60]}")
    return False

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def clean_price(price_str) -> float:
    """
    Safely extract integer rupee price.
    ₹1,299.00 → 1299  |  1,23,456 → 123456  |  garbage → inf
    """
    if not price_str:
        return float('inf')
    s = re.sub(r'[₹,\s]', '', str(price_str).strip())
    s = s.split('.')[0]                 # drop decimal part
    s = RE_PRICE_CLEAN.sub('', s)       # strip remaining non-digits
    try:
        val = int(s)
        return float(val) if val > 10 else float('inf')
    except (ValueError, OverflowError):
        return float('inf')

# PERF: Shared session for requests (connection pooling)
_session = requests.Session()
_session.headers.update({
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
})

def _cffi_get(url: str, timeout: int = 10, extra_headers: dict = None):
    """
    PERF improvements:
    - Reduced default timeout: 14s → 10s
    - Pick ONE random browser instead of trying two sequentially
    - Only fall back to a second browser on connection error, not on any error
    """
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "User-Agent": random.choice(USER_AGENTS),
        **(extra_headers or {}),
    }
    # PERF: Try one browser; only retry with a different one on failure
    primary = random.choice(BROWSERS[:3])   # pick from fast modern browsers
    fallback = "chrome116"
    for browser in (primary, fallback):
        try:
            r = cffi_requests.get(url, impersonate=browser, timeout=timeout, headers=headers)
            if r.status_code == 200:
                return r
            # Non-200 from server — no point retrying with another browser
            print(f"    [{browser}] HTTP {r.status_code} — skipping fallback")
            return None
        except Exception as e:
            print(f"    [{browser}] {e}")
            if browser == fallback:
                return None
    return None

# ─────────────────────────────────────────────
# SCRAPERS
# ─────────────────────────────────────────────
_RE_TITLE_JUNK = re.compile(
    r'^(sponsored|ad|advertisement|\d[\d\.]* out of \d[\d\.]* stars?.*)',
    re.I
)

def _amazon_title(card) -> str:
    """
    Amazon title extraction — strictly from product-title elements only.

    Root cause of the bug: 'h2 a span' and 'h2 span' both match rating spans
    ("4.1 out of 5 stars, rating details") and "SponsoredSponsored" nodes that
    happen to live inside or adjacent to h2 in the card. We now:
      1. Use the most specific selectors first (a-text-normal, a-size-medium).
      2. Reject any candidate that looks like a rating or "Sponsored" string.
      3. Only fall back to broader selectors if specific ones fail.
    """
    def _valid(t: str) -> bool:
        return len(t) > 8 and not _RE_TITLE_JUNK.match(t.strip())

    # Most specific — used by the current Amazon grid layout
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
            if _valid(t):
                return t

    # Fallback: any span inside h2 that isn't a rating/sponsored label
    h2 = card.select_one('h2')
    if h2:
        for span in h2.find_all('span'):
            t = span.get_text(strip=True)
            if _valid(t):
                return t

    # Last resort: aria-label on the product link
    a = card.select_one('a[href*="/dp/"][aria-label]')
    if a:
        t = a.get('aria-label', '').strip()
        if _valid(t):
            return t

    return ''


def _amazon_price(card) -> float:
    """
    Try multiple price selectors in order of reliability.
    a-price-whole alone is missing the decimal; a-offscreen has the full formatted price.
    We prefer a-offscreen because it has the complete value, then fall back.
    """
    # Most reliable: the screen-reader span which has ₹1,299.00
    for sel in ['.a-price .a-offscreen', 'span.a-offscreen']:
        els = card.select(sel)
        for el in els:
            p = clean_price(el.get_text(strip=True))
            if p != float('inf'):
                return p

    # a-price-whole is the big integer before the decimal widget
    pe = card.select_one('.a-price-whole')
    if pe:
        p = clean_price(pe.get_text(strip=True))
        if p != float('inf'):
            return p

    # Last resort: grep raw text for ₹ symbol
    m = RE_PRICE_SYMBOL.search(card.get_text())
    if m:
        return clean_price(m.group(1))

    return float('inf')


def scrape_amazon(query: str):
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
        all_cards = soup.find_all('div', {'data-component-type': 's-search-result'})
        print(f"  [Amazon] Cards found: {len(all_cards)}")

        # Sponsored detection: Amazon marks sponsored cards with AdType meta or a
        # visible "Sponsored" label. data-component-type is 's-search-result' for BOTH
        # organic and sponsored — the difference is only the inner label.
        organic, sponsored = [], []
        for card in all_cards:
            if card.find('span', string=RE_SPONSORED):
                sponsored.append(card)
            else:
                organic.append(card)
        print(f"  [Amazon] Organic: {len(organic)}, Sponsored: {len(sponsored)}")

        def parse_card(card):
            title_text = _amazon_title(card)
            if not title_text:
                return None

            # Filter BEFORE price extraction to skip irrelevant cards fast
            if not is_correct_product(query, title_text):
                return None

            price = _amazon_price(card)
            if price == float('inf'):
                print(f"  [Amazon] No price for: {title_text[:50]}")
                return None

            rating_val = "N/A"
            elem = card.select_one('span.a-icon-alt')
            if elem:
                m = RE_RATING_INLINE.search(elem.get_text())
                if m:
                    rating_val = m.group(1)

            link_elem = card.select_one('a[href*="/dp/"]')
            link = urljoin("https://www.amazon.in", link_elem['href']) if link_elem else url
            img_elem = card.select_one('img.s-image')
            img = img_elem.get('src', '') if img_elem else ''

            print(f"  [Amazon] ✅ {title_text[:50]} @ ₹{price:,.0f} ⭐{rating_val}")
            return {"price": price, "display_price": f"₹{price:,.0f}",
                    "rating": rating_val, "link": link, "title": title_text[:70], "image": img}

        for card in organic:
            r = parse_card(card)
            if r:
                return r
        print("  [Amazon] Falling back to sponsored cards...")
        for card in sponsored:
            r = parse_card(card)
            if r:
                return r

    except Exception as e:
        print(f"  [Amazon] Error: {e}")
    return None


def scrape_flipkart(query: str):
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
            price_match = RE_PRICE_SYMBOL.search(text_content)
            if not price_match:
                continue
            price = clean_price(price_match.group(1))
            if price == float('inf'):
                continue

            title_text = ""
            img = link.select_one('img')
            if img and len(img.get('alt', '')) > 5:
                title_text = img['alt']
            if not title_text:
                for d in link.find_all('div'):
                    t = d.get_text(strip=True)
                    if len(t) > 10 and '₹' not in t and not re.match(r'^[1-5]\.[0-9]', t):
                        title_text = t
                        break
            if not title_text:
                continue

            # PERF: Filter early
            if not is_correct_product(query, title_text):
                continue

            rating_val = "N/A"
            for div in link.find_all('div'):
                if RE_RATING_DIV.match(div.get_text(strip=True)):
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


def scrape_reliance(query: str):
    safe_q = urllib.parse.quote(query)
    url = (f"https://www.reliancedigital.in/ext/raven-api/catalog/v1.0/products"
           f"?q={safe_q}&page_no=1&page_size=24")
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, */*",
        "Accept-Language": "en-IN,en;q=0.9",
        "Referer": "https://www.reliancedigital.in/",
    }
    try:
        # PERF: Use shared session for connection pooling
        r = _session.get(url, headers=headers, timeout=10)
        r.encoding = 'utf-8'
        print(f"  [Reliance] API status: {r.status_code}")
        if r.status_code != 200:
            return None
        data = r.json()
        items = data.get('items', []) if isinstance(data, dict) else []
        print(f"  [Reliance] {len(items)} items returned")

        for item in items:
            name = item.get('name', '')
            if not name:
                continue
            # PERF: Filter early before price parsing
            if not is_correct_product(query, name):
                continue
            slug = item.get('slug', '')
            price_data = item.get('price', {})
            price = (price_data.get('effective', {}).get('min', 0)
                     if isinstance(price_data, dict) else 0)
            if not price or price <= 0:
                continue

            rd_img = ''
            medias = item.get('medias', [])
            if medias and isinstance(medias, list):
                first = medias[0]
                if isinstance(first, dict):
                    rd_img = first.get('url') or first.get('image') or ''
                    if rd_img and not rd_img.startswith('http'):
                        rd_img = 'https://www.reliancedigital.in' + rd_img

            print(f"  [Reliance] ✅ {name[:50]} @ ₹{price:,}")
            return {"price": float(price), "display_price": f"₹{price:,}",
                    "rating": "N/A",
                    "link": (f"https://www.reliancedigital.in/product/{slug}"
                             if slug else f"https://www.reliancedigital.in/search?q={safe_q}"),
                    "title": name[:70], "image": rd_img}

    except Exception as e:
        print(f"  [Reliance] Error: {e}")
    return None


def scrape_vijaysales(query: str):
    safe_q = urllib.parse.quote(query)
    search_url = f"https://www.vijaysales.com/search?q={safe_q}"
    try:
        resp = _cffi_get(search_url, extra_headers={"Referer": "https://www.vijaysales.com/"})
        if not resp:
            return None
        print(f"  [VijayS] Status: {resp.status_code}, len={len(resp.text)}")
        soup = BeautifulSoup(resp.text, 'lxml')

        # ── Card discovery ────────────────────────────────────────────────────
        # Try known selectors from most to least specific.
        # IMPORTANT: do NOT use generic fallbacks like 'a[href*="/"]' — those
        # match nav/footer/promo links and return completely wrong products.
        cards = (
            soup.select('a.productcollection__item') or
            soup.select('div.product-card a.product-link') or
            soup.select('li.product__item a') or
            soup.select('div.plp-product-card a')
        )

        # Tighter fallback: only <a> tags that are inside a search-result
        # container AND contain both a price (₹) and a non-trivial text block.
        if not cards:
            result_root = (
                soup.select_one('div[class*="search-result"]') or
                soup.select_one('div[class*="product-listing"]') or
                soup.select_one('section[class*="product"]') or
                soup.select_one('main')
            )
            if result_root:
                cards = [
                    a for a in result_root.select('a[href]')
                    if RE_PRICE_SYMBOL.search(a.get_text())
                    and len(a.get_text(strip=True)) > 20   # must have substantive text
                ]
        print(f"  [VijayS] Cards found: {len(cards)}")

        for card in cards:
            # ── Price ──────────────────────────────────────────────────────
            price = float('inf')
            for sel in ['div.price span', 'span.price', 'div.product-price',
                        'span.selling-price', 'p.price', 'span[class*="price"]']:
                el = card.select_one(sel)
                if el:
                    price = clean_price(el.get_text(strip=True))
                    if price != float('inf'):
                        break
            if price == float('inf'):
                m = RE_PRICE_SYMBOL.search(card.get_text())
                if m:
                    price = clean_price(m.group(1))
            if price == float('inf'):
                continue

            # ── Title ──────────────────────────────────────────────────────
            title = ''
            for sel in ['div.name', 'p.name', 'div.product-name', 'p.product-name',
                        'span.product-name', 'div.title', 'p.title',
                        'h2', 'h3', 'h4', 'span.name']:
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
            if not title or len(title) < 5:
                continue

            print(f"  [VijayS] Candidate: ₹{price:,.0f} | {title[:60]}")
            if not is_correct_product(query, title):
                continue

            href = card.get('href', '')
            link = urljoin("https://www.vijaysales.com", href) if href else search_url
            img_el = card.select_one('img')
            img = ''
            if img_el:
                img = (img_el.get('data-original') or img_el.get('data-src') or img_el.get('src') or '')

            print(f"  [VijayS] ✅ {title[:50]} @ ₹{price:,.0f}")
            return {"price": price, "display_price": f"₹{price:,.0f}",
                    "rating": "N/A", "link": link, "title": title[:70], "image": img}

        print("  [VijayS] No matching product found")
    except Exception as e:
        print(f"  [VijayS] Error: {e}")
    return None


def scrape_snapdeal(query: str):
    safe_q = urllib.parse.quote(query)
    url = f"https://www.snapdeal.com/search?keyword={safe_q}&sort=rlvncy"
    try:
        resp = _cffi_get(url)
        if not resp:
            return None
        print(f"  [Snapdeal] Status: {resp.status_code}")
        soup = BeautifulSoup(resp.text, "lxml")

        # Try multiple known card selectors (Snapdeal has changed layouts)
        cards = (soup.select("div.product-tuple-listing") or
                 soup.select("div.product-tuple-description") or
                 soup.select("div[class*='product-tuple']"))
        print(f"  [Snapdeal] Cards found: {len(cards)}")

        for card in cards:
            # ── Title ──────────────────────────────────────────────────────
            title_el = (card.select_one("p.product-title") or
                        card.select_one("p[class*='title']") or
                        card.select_one("div[class*='title']") or
                        card.select_one("span[class*='title']"))
            if not title_el:
                continue
            title_text = title_el.get_text(strip=True)
            if len(title_text) < 5:
                continue

            # ── Price ──────────────────────────────────────────────────────
            price_el = (card.select_one("span.product-price") or
                        card.select_one("span[class*='price']") or
                        card.select_one("div[class*='price']"))
            price_val = float('inf')
            if price_el:
                price_val = clean_price(price_el.get_text())
            if price_val == float('inf'):
                m = RE_PRICE_SYMBOL.search(card.get_text())
                if m:
                    price_val = clean_price(m.group(1))
            if price_val == float('inf'):
                continue

            print(f"  [Snapdeal] Candidate: ₹{price_val:,.0f} | {title_text[:60]}")
            if not is_correct_product(query, title_text):
                continue

            link_el = card.select_one("a.dp-widget-link, a[href*='/product/']")
            img_el  = card.select_one("img.product-image, img[class*='product']")
            print(f"  [Snapdeal] ✅ {title_text[:50]} @ ₹{price_val:,.0f}")
            return {"price": price_val, "display_price": f"₹{price_val:,.0f}",
                    "rating": "N/A",
                    "link": link_el["href"] if link_el else url,
                    "title": title_text[:70],
                    "image": img_el.get("src", "") if img_el else ""}

        print("  [Snapdeal] No matching product found")
    except Exception as e:
        print(f"  [Snapdeal] Error: {e}")
    return None


def scrape_shopclues(query: str):
    safe_q = urllib.parse.quote(query)
    url = f"https://www.shopclues.com/search?q={safe_q}"
    try:
        resp = _cffi_get(url)
        if not resp:
            return None
        print(f"  [ShopClues] Status: {resp.status_code}")
        soup = BeautifulSoup(resp.text, "lxml")

        # Multiple possible card containers
        cards = (soup.select("div.column.search_blocks") or
                 soup.select("section.section_featured_product div.column") or
                 soup.select("div.search_blocks") or
                 soup.select("div[class*='product_grid'] div.column"))
        print(f"  [ShopClues] Cards found: {len(cards)}")

        for card in cards:
            # ── Title ──────────────────────────────────────────────────────
            title_el = (card.select_one("h2") or
                        card.select_one("p.prod_name") or
                        card.select_one("div[class*='name']") or
                        card.select_one("span[class*='name']"))
            if not title_el:
                continue
            title_text = title_el.get_text(strip=True)
            if len(title_text) < 5:
                continue

            # ── Price ──────────────────────────────────────────────────────
            price_el = (card.select_one(".p_price") or
                        card.select_one("span[class*='price']") or
                        card.select_one("div[class*='price']"))
            price_val = float('inf')
            if price_el:
                price_val = clean_price(price_el.get_text())
            if price_val == float('inf'):
                m = RE_PRICE_SYMBOL.search(card.get_text())
                if m:
                    price_val = clean_price(m.group(1))
            if price_val == float('inf'):
                continue

            print(f"  [ShopClues] Candidate: ₹{price_val:,.0f} | {title_text[:60]}")
            if not is_correct_product(query, title_text):
                continue

            link_el = card.select_one("a")
            img_el  = card.select_one("img")
            print(f"  [ShopClues] ✅ {title_text[:50]} @ ₹{price_val:,.0f}")
            return {"price": price_val, "display_price": f"₹{price_val:,.0f}",
                    "rating": "N/A",
                    "link": link_el["href"] if link_el else url,
                    "title": title_text[:70],
                    "image": img_el.get("src", "") if img_el else ""}

        print("  [ShopClues] No matching product found")
    except Exception as e:
        print(f"  [ShopClues] Error: {e}")
    return None


# ─────────────────────────────────────────────
# ORCHESTRATOR
# ─────────────────────────────────────────────
STORES_TO_SCRAPE = {
    "Amazon":         scrape_amazon,
    "Flipkart":       scrape_flipkart,
    "Reliance Digital": scrape_reliance,
    "Vijay Sales":    scrape_vijaysales,
    "Snapdeal":       scrape_snapdeal,
    "ShopClues":      scrape_shopclues,
}
BEST_STORES = frozenset({"Amazon", "Flipkart", "Reliance Digital"})

# PERF: Persistent thread pool — avoid re-creating it on every call
_executor = ThreadPoolExecutor(max_workers=len(STORES_TO_SCRAPE))

def get_product_data(query: str, timeout: float = 20.0) -> dict:
    """
    Key speed improvements vs original:
    1. Pre-compiled regexes (module-level) — no re-compile per call.
    2. Early `is_correct_product` checks before heavy price parsing.
    3. Reduced HTTP timeout: 14 → 10 s; single-browser retry instead of sequential.
    4. Shared requests.Session for Reliance (connection pooling).
    5. Hard wall-clock timeout on the whole batch via `wait(timeout=)`.
       Slow scrapers are simply abandoned rather than blocking the result.
    6. Persistent ThreadPoolExecutor (module-level) avoids thread-creation overhead.
    7. frozenset for brand/accessory lookups → O(1) instead of list scan.
    """
    t0 = time.time()
    safe_q = urllib.parse.quote(query)

    # Default results
    results: dict[str, dict] = {
        store: {
            "price": float('inf'),
            "display_price": "Not Found",
            "rating": "N/A",
            "link": f"https://www.{store.lower().replace(' ', '')}.com/search?q={safe_q}",
            "title": "No match",
            "image": "",
        }
        for store in STORES_TO_SCRAPE
    }

    print(f"\n[Searching] {query}")

    # PERF: Submit all tasks to the persistent pool
    future_to_store = {
        _executor.submit(scraper, query): store
        for store, scraper in STORES_TO_SCRAPE.items()
    }

    # PERF: Hard wall-clock deadline — don't wait for stragglers beyond `timeout` seconds
    done, pending = wait(future_to_store, timeout=timeout)

    for future in done:
        store = future_to_store[future]
        try:
            result = future.result()
            if result and isinstance(result, dict) and result.get('price', 0) > 0:
                results[store] = result
        except Exception as e:
            print(f"  [{store}] Exception: {e}")

    for future in pending:
        store = future_to_store[future]
        print(f"  [{store}] Timed out after {timeout}s — skipped")
        future.cancel()

    # Best price (Amazon / Flipkart / Reliance only)
    best_store, lowest_price = None, float('inf')
    for store in BEST_STORES:
        price = results[store].get('price', float('inf'))
        if price < lowest_price:
            lowest_price, best_store = price, store

    best_data = ({"store": best_store, "display_price": f"₹{lowest_price:,.0f}"}
                 if best_store and lowest_price != float('inf') else None)

    print(f"  [Total] {time.time() - t0:.1f}s")
    return {"stores": results, "best": best_data}


if __name__ == "__main__":
    result = get_product_data("iphone 13")
    print(json.dumps(result, indent=2))
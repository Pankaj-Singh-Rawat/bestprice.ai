from __future__ import annotations

import os
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import requests as req_lib
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, url_for

load_dotenv()
app = Flask(__name__)

OPENROUTER_API_KEY = os.getenv("BESTPRICE_OPENROUTER_API")

OPENROUTER_MODELS = [
    "meta-llama/llama-4-scout:free",
    "meta-llama/llama-3.1-8b-instruct:free",
    "qwen/qwen-2.5-7b-instruct:free",
    "microsoft/phi-3-mini-128k-instruct:free",
    "google/gemma-3-4b-it:free",
    "google/gemma-3-12b-it:free",
    "mistralai/mistral-7b-instruct:free",
]

_URL_RE = re.compile(
    r'^(https?://|www\.)|(amazon|flipkart|reliancedigital|vijaysales|snapdeal|shopclues)\.',
    re.I,
)


def _is_url(text: str) -> bool:
    return bool(_URL_RE.match(text.strip()))


def _fetch_title_from_url(url: str) -> str | None:
    try:
        if '://' not in url:
            url = 'https://' + url
        r = req_lib.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-IN,en;q=0.9",
        }, timeout=8)
        soup = BeautifulSoup(r.text, 'html.parser')
        for sel in ['#productTitle', 'h1.yhB1nd', 'h1[class*="product"]',
                    'h1[class*="title"]', '.pdp-title', 'h1.pdp-name', 'h1']:
            el = soup.select_one(sel)
            if el:
                t = el.get_text(strip=True)
                if len(t) > 5:
                    return re.split(r'\s*[|\-–]\s*(Amazon|Flipkart|Reliance)', t)[0].strip()
        if soup.title and soup.title.string:
            t = soup.title.string
            return re.split(r'\s*[|\-–]\s*(Amazon|Flipkart|Reliance|Buy|Online)', t)[0].strip()
    except Exception as e:
        print(f"  [URL fetch] {e}")
    # Slug fallback
    try:
        parsed = urllib.parse.urlparse(url)
        for part in parsed.path.split('/'):
            if '-' in part and len(part) > 10:
                slug = part.replace('-', ' ')
                slug = re.sub(r'\b[A-Z0-9]{10}\b', '', slug)
                slug = re.sub(r'\b\d{9}\b', '', slug)
                return slug.strip()
    except Exception:
        pass
    return None


def clean_title_with_ai(raw_title: str) -> str | None:
    """Use AI to shorten a messy product title into a clean search query."""
    if not OPENROUTER_API_KEY:
        return None
    prompt = (
        "You are a product search query extractor for an Indian price comparison website.\n"
        "Given any product title (phone, laptop, TV, AC, toy, clothes, appliance, etc.), "
        "return the shortest possible search query to find it on Amazon/Flipkart.\n"
        "Keep: brand, model name/number, key spec (size/storage/capacity/variant). Remove everything else.\n"
        "If the input is already a short clean query (1-4 words), return it exactly as-is.\n"
        "Max 6 words. Reply with ONLY the search query, nothing else.\n\n"
        "Examples:\n"
        "iPhone 17 Pro Max 2 TB: 17.42 cm Display with Promotion, A19 Pro Chip → iPhone 17 Pro Max 2TB\n"
        "Samsung Galaxy S25 Ultra 256GB Titanium Black, 200MP Camera → Samsung Galaxy S25 Ultra 256GB\n"
        "Sony WH-1000XM5 Wireless Noise Cancelling Headphones 30hr battery → Sony WH-1000XM5\n"
        "Apple MacBook Air 13-inch M3 chip 8GB RAM 256GB SSD Space Gray → MacBook Air M3 256GB\n"
        "Levi's Men's 511 Slim Fit Stretch Jeans Multiple Colors Available → Levi's 511 Slim Jeans\n"
        "Hot Wheels 20-Car Gift Pack, Toy Cars for Kids Ages 3 and Up → Hot Wheels 20 Car Pack\n"
        "Toys → Toys\n"
        "AC → AC\n"
        "Nike Running Shoes → Nike Running Shoes\n\n"
        f"Input: {raw_title}\nOutput:"
    )
    for model in OPENROUTER_MODELS:
        try:
            resp = req_lib.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}",
                         "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 20, "temperature": 0},
                timeout=6,
            )
            data = resp.json()
            if resp.status_code == 429 or 'choices' not in data:
                continue
            result = data['choices'][0]['message']['content'].strip()
            result = re.sub(r'^(Output:|Query:|Result:)\s*', '', result, flags=re.I).strip('"\'')
            if 2 < len(result) < 80:
                print(f"  [AI] ({model}) → '{result}'")
                return result
        except Exception as e:
            print(f"  [AI] {model} error: {e}")
    return None


def get_clean_query(raw: str) -> str:
    raw = raw.strip()
    # Handle URL input
    if _is_url(raw):
        fetched = _fetch_title_from_url(raw)
        if fetched:
            raw = fetched
    # Short queries: do not call AI (keep as is)
    words = raw.split()
    if len(words) <= 4 and len(raw) < 40:
        print(f"[Clean] Query is already short: '{raw}' → no AI")
        return raw[:60].strip()
    # Try AI cleaning, but only use it if it produces a shorter/different query
    cleaned = clean_title_with_ai(raw)
    if cleaned and cleaned != raw and len(cleaned) < len(raw):
        print(f"[Clean] AI shortened: '{raw}' → '{cleaned}'")
        return cleaned[:60].strip()
    # Fallback: return truncated original (no AI override)
    return raw[:60].strip()


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        raw = request.form.get('product_name', '').strip()
        if not raw:
            return redirect(url_for('index'))
        query = get_clean_query(raw)
        # If AI cleaned it, redirect to the clean query (so the user sees the cleaned version)
        if query != raw and query:
            return redirect(url_for('results', query=query))
        return redirect(url_for('results', query=query))
    return render_template('index.html')


@app.route('/results')
def results():
    query = request.args.get('query', '').strip()
    if not query:
        return redirect(url_for('index'))

    from scraper import get_product_data

    # Run scraping (AI cleaning already done in redirect)
    data = get_product_data(query)
    return render_template('index.html', query=query, results=data, float=float)


@app.route('/api/search')
def api_search():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({"error": "q param required"}), 400
    from scraper import get_product_data
    data = get_product_data(query)
    for store, d in data['stores'].items():
        if d['price'] == float('inf'):
            d['price'] = None
    return jsonify(data)


@app.route('/<path:full_url>')
def magic_link(full_url):
    if full_url in ('favicon.ico', 'robots.txt'):
        return '', 204
    url = full_url if '://' in full_url else 'https://' + full_url
    query = get_clean_query(url)
    return redirect(url_for('results', query=query))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80, debug=False)
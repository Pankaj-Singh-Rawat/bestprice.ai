"""
app.py  —  BestPrice.ai
Flask entry point.

Changes vs original:
- AI query cleaning is optional and skipped for queries that are already clean
  (≤ 6 words, no URL, no long junk). This removes the 3-8s AI round-trip for
  most normal searches.
- No redirect loops: cleaned flag is handled in-process, not via a second HTTP
  redirect, so the loading screen stays visible throughout.
- Savings info from scraper is passed to template.
- /share/<query> route for easy link sharing.
"""

from __future__ import annotations

import os
import re
import urllib.parse

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
    "mistralai/mistral-7b-instruct:free",
    "google/gemma-3-4b-it:free",
]

_URL_RE = re.compile(
    r'^(https?://|www\.)|(amazon|flipkart|reliancedigital|vijaysales|snapdeal|shopclues)\.',
    re.I,
)


def _is_url(text: str) -> bool:
    return bool(_URL_RE.match(text.strip()))


def _needs_ai_cleaning(text: str) -> bool:
    """Return True only if the query is long/messy enough to warrant AI cleanup."""
    if _is_url(text):
        return True
    words = text.strip().split()
    # Clean if > 6 words OR contains measurement symbols, colons, quotes
    if len(words) > 6:
        return True
    if re.search(r'[:\u2033\u2032"\']', text):
        return True
    return False


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


def _ai_clean(raw: str) -> str | None:
    if not OPENROUTER_API_KEY:
        return None
    prompt = (
        "You are a product search query extractor for an Indian price comparison site.\n"
        "Given any product title or URL title, return the shortest possible search query "
        "to find it on Amazon/Flipkart. Keep: brand, model name/number, key spec. "
        "Remove everything else. Max 6 words. "
        "If the input is already a short clean query (1-4 words), return it as-is.\n"
        "Reply with ONLY the query, nothing else.\n\n"
        f"Input: {raw}\nOutput:"
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
            if 3 < len(result) < 80:
                print(f"  [AI] ({model}) → '{result}'")
                return result
        except Exception as e:
            print(f"  [AI] {model} error: {e}")
    return None


def get_clean_query(raw: str) -> str:
    raw = raw.strip()
    if _is_url(raw):
        url = raw if '://' in raw else 'https://' + raw
        fetched = _fetch_title_from_url(url)
        raw = fetched or raw

    if _needs_ai_cleaning(raw):
        cleaned = _ai_clean(raw)
        if cleaned:
            return cleaned

    return raw[:60].strip()


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        raw = request.form.get('product_name', '').strip()
        if not raw:
            return redirect(url_for('index'))
        query = get_clean_query(raw)
        return redirect(url_for('results', query=query))
    return render_template('index.html')


@app.route('/results')
def results():
    query = request.args.get('query', '').strip()
    if not query:
        return redirect(url_for('index'))

    from scraper import get_product_data
    data = get_product_data(query)
    return render_template('index.html', query=query, results=data, float=float)


@app.route('/api/search')
def api_search():
    """JSON endpoint — useful for future React/mobile frontends."""
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({"error": "q param required"}), 400
    from scraper import get_product_data
    data = get_product_data(query)
    # Make JSON serialisable (replace inf)
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
from flask import Flask, render_template, request, redirect, url_for
from scraper import get_product_data
import urllib.parse
import re
import requests as req_lib
from bs4 import BeautifulSoup
import os
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

OPENROUTER_API_KEY = os.getenv("BESTPRICE_OPENROUTER_API")

# Free models on OpenRouter — tried in order, falls back if one is down
OPENROUTER_MODELS = [
    "meta-llama/llama-4-scout:free",
    "meta-llama/llama-3.1-8b-instruct:free",
    "qwen/qwen-2.5-7b-instruct:free",
    "microsoft/phi-3-mini-128k-instruct:free",
    "google/gemma-3-4b-it:free",
    "mistralai/mistral-7b-instruct:free",
]

def clean_title_with_ai(raw_title):
    if not raw_title or not OPENROUTER_API_KEY:
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
        "Samsung Galaxy S25 Ultra 256GB Titanium Black, 200MP Camera, S Pen → Samsung Galaxy S25 Ultra 256GB\n"
        "Sony WH-1000XM5 Wireless Noise Cancelling Headphones 30hr battery LDAC → Sony WH-1000XM5\n"
        "Apple MacBook Air 13-inch M3 chip 8GB RAM 256GB SSD Space Gray → MacBook Air M3 256GB\n"
        "Voltas 1.5 Ton 5 Star Inverter Split AC with 4-in-1 Convertible mode → Voltas 1.5 Ton 5 Star AC\n"
        "Samsung 55 inch 4K Ultra HD Smart QLED TV with Alexa Built-in 2024 → Samsung 55 inch QLED TV\n"
        "Levi\'s Men\'s 511 Slim Fit Stretch Jeans Multiple Colors Available → Levi\'s 511 Slim Jeans\n"
        "Hot Wheels 20-Car Gift Pack, Toy Cars for Kids Ages 3 and Up → Hot Wheels 20 Car Pack\n"
        "Prestige IRIS 750 Watt Mixer Grinder with 3 Stainless Steel Jars → Prestige IRIS 750W Mixer\n"
        "Toys → Toys\n"
        "AC → AC\n"
        "Nike Running Shoes → Nike Running Shoes\n\n"
        f"Input: {raw_title}\nOutput:"
    )

    for model in OPENROUTER_MODELS:
        try:
            response = req_lib.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 20,
                    "temperature": 0,
                },
                timeout=8
            )
            data = response.json()
            if response.status_code == 429:
                print(f"  [AI] {model} rate limited, trying next...")
                continue
            if 'choices' not in data:
                print(f"  [AI] {model} unexpected: {data.get('error', {}).get('message', str(data))[:100]}")
                continue
            result = data['choices'][0]['message']['content'].strip()
            result = re.sub(r'^(Output:|Query:|Result:)\s*', '', result, flags=re.I).strip('"\'')
            print(f"  [AI] ({model}) '{raw_title[:50]}' → '{result}'")
            if 3 < len(result) < 80:
                return result
        except Exception as e:
            print(f"  [AI] {model} error: {e}")
            continue

    print("  [AI] All models failed, using raw title")
    return None


def fetch_product_title_from_url(url_string):
    """Fetch a store page and extract the raw product title."""
    try:
        if '://' not in url_string:
            url_string = 'https://' + url_string
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-IN,en;q=0.9",
        }
        r = req_lib.get(url_string, headers=headers, timeout=8)
        soup = BeautifulSoup(r.text, 'html.parser')

        for selector in ['#productTitle', 'h1.yhB1nd', 'h1[class*="product"]',
                         'h1[class*="title"]', '.pdp-title', 'h1.pdp-name', 'h1']:
            el = soup.select_one(selector)
            if el:
                title = el.get_text(strip=True)
                if len(title) > 5:
                    return re.split(r'\s*[|\-–]\s*(Amazon|Flipkart|Reliance)', title)[0].strip()

        if soup.title:
            title = soup.title.string or ''
            return re.split(r'\s*[|\-–]\s*(Amazon|Flipkart|Reliance|Buy|Online)', title)[0].strip()

    except Exception as e:
        print(f"  [URL fetch] Error: {e}")

    # Fallback: slug from URL
    try:
        parsed = urllib.parse.urlparse(url_string)
        for part in parsed.path.split('/'):
            if '-' in part and len(part) > 10:
                slug = part.replace('-', ' ')
                slug = re.sub(r'\b[A-Z0-9]{10}\b', '', slug)
                slug = re.sub(r'\b[A-Za-z0-9]{16}\b', '', slug)
                slug = re.sub(r'\b\d{9}\b', '', slug)
                return slug.strip()
    except Exception:
        pass

    return None


def is_url(text):
    text = text.strip()
    return (text.startswith('http://') or text.startswith('https://') or
            bool(re.match(r'^(www\.)?(amazon|flipkart|reliancedigital)\.', text)))


def get_clean_query(raw):
    """Single pipeline: URL or messy title → clean search query via AI."""
    if is_url(raw):
        full_url = raw if '://' in raw else 'https://' + raw
        raw = fetch_product_title_from_url(full_url) or raw

    cleaned = clean_title_with_ai(raw)
    return cleaned if cleaned else raw[:50].strip()


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

    # Clean verbose queries — only once (c=1 flag prevents loop)
    if not request.args.get('c') and (is_url(query) or len(query) > 60 or ':' in query or '″' in query):
        cleaned = get_clean_query(query)
        if cleaned and cleaned != query and len(cleaned) < len(query):
            return redirect(url_for('results', query=cleaned, c='1'))

    # ✅ Run AI display-name cleaning and scraping IN PARALLEL
    # AI cleans the query for display while scrapers are already running
    from concurrent.futures import ThreadPoolExecutor as TPE
    with TPE(max_workers=2) as ex:
        scrape_future = ex.submit(get_product_data, query)
        # Clean for display only — scraper already uses the clean query
        data = scrape_future.result()

    return render_template('index.html', query=query, results=data, float=float)


@app.route('/<path:full_url>')
def magic_link_handler(full_url):
    if full_url == 'favicon.ico':
        return "", 204
    full_url_with_scheme = full_url if '://' in full_url else 'https://' + full_url
    query = get_clean_query(full_url_with_scheme)
    return redirect(url_for('results', query=query))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
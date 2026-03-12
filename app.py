from flask import Flask, render_template, request, redirect, url_for
from scraper import get_product_data
import urllib.parse
import re

app = Flask(__name__)

def extract_query_from_url(url_string):
    """Extract a clean product name from a store URL."""
    try:
        parsed = urllib.parse.urlparse(url_string)
        path_parts = parsed.path.split('/')
        query = ""
        for part in path_parts:
            if '-' in part and len(part) > 10:
                query = part.replace('-', ' ')
                query = re.sub(r'\b[A-Z0-9]{10}\b', '', query)   # Amazon ASINs
                query = re.sub(r'\b[A-Za-z0-9]{16}\b', '', query) # Flipkart IDs
                query = re.sub(r'\b\d{9}\b', '', query)           # Reliance IDs
                break
        return query.strip() or None
    except Exception:
        return None

def is_url(text):
    """Check if the input looks like a URL."""
    text = text.strip()
    return (text.startswith('http://') or
            text.startswith('https://') or
            re.match(r'^(www\.)?(amazon|flipkart|reliancedigital)\.', text))

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        raw = request.form.get('product_name', '').strip()
        if not raw:
            return redirect(url_for('index'))

        # If user pasted a URL, extract product name from it
        if is_url(raw):
            full_url = raw if '://' in raw else 'https://' + raw
            query = extract_query_from_url(full_url) or "Smartphone"
        else:
            query = raw

        return redirect(url_for('results', query=query))
    return render_template('index.html')

@app.route('/results')
def results():
    query = request.args.get('query')
    if not query:
        return redirect(url_for('index'))
    data = get_product_data(query)
    return render_template('index.html', query=query, results=data, float=float)

@app.route('/<path:full_url>')
def magic_link_handler(full_url):
    if full_url in ('favicon.ico',):
        return "", 204
    try:
        parsed_url = urllib.parse.urlparse(full_url)
        path_parts = parsed_url.path.split('/')
        query = ""
        for part in path_parts:
            if '-' in part and len(part) > 10:
                query = part.replace('-', ' ')
                query = re.sub(r'\b[A-Z0-9]{10}\b', '', query)
                query = re.sub(r'\b[A-Za-z0-9]{16}\b', '', query)
                query = re.sub(r'\b\d{9}\b', '', query)
                break
        if not query:
            query = "Smartphone"
    except Exception:
        query = "Smartphone"
    return redirect(url_for('results', query=query.strip()))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
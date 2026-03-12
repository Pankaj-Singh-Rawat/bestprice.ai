from flask import Flask, render_template, request, redirect, url_for
from scraper import get_product_data
import urllib.parse
import re

app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        query = request.form.get('product_name')
        return redirect(url_for('results', query=query))
    return render_template('index.html')

@app.route('/results')
def results():
    query = request.args.get('query')
    if not query:
        return redirect(url_for('index'))
    
    data = get_product_data(query)
    # We pass 'float=float' so Jinja can check for infinity (Not Found) items
    return render_template('index.html', query=query, results=data, float=float)


@app.route('/<path:full_url>')
def magic_link_handler(full_url):
    # Ignore background browser requests
    if full_url == 'favicon.ico':
        return "", 204
        
    try:
        parsed_url = urllib.parse.urlparse(full_url)
        path_parts = parsed_url.path.split('/')
        query = ""
        
        for part in path_parts:
            # Find the part of the URL that has hyphens (the product name)
            if '-' in part and len(part) > 10:
                query = part.replace('-', ' ')
                
                # Clean up specific store IDs so they don't mess up the search:
                query = re.sub(r'\b[A-Z0-9]{10}\b', '', query) # Removes Amazon ASINs
                query = re.sub(r'\b[A-Za-z0-9]{16}\b', '', query) # Removes Flipkart Item IDs
                query = re.sub(r'\b\d{9}\b', '', query) # Removes Reliance Digital IDs
                break
                
        if not query:
            query = "Smartphone" # Fallback if we can't read the URL
            
    except Exception:
        query = "Smartphone"
        
    # Redirect to the search results page with our clean product name
    return redirect(url_for('results', query=query.strip()))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
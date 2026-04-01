# BestPrice.ai 🏆

**India's fastest real-time price comparison tool.**  
Search any product or paste a store link — BestPrice.ai scrapes Amazon, Flipkart, and Reliance Digital simultaneously and tells you where it's cheapest.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [How It Works](#how-it-works)
- [Setup & Installation](#setup--installation)
- [Environment Variables](#environment-variables)
- [Running Locally](#running-locally)
- [Sharing on Local Network](#sharing-on-local-network)
- [Deployment](#deployment)
- [Scraper Architecture](#scraper-architecture)
- [AI Query Cleaning](#ai-query-cleaning)
- [URL Intelligence](#url-intelligence)
- [Product Matching Logic](#product-matching-logic)
- [Known Limitations](#known-limitations)
- [Contributing](#contributing)

---

## Overview

BestPrice.ai is a Flask web application that lets Indian shoppers instantly compare product prices across the three biggest electronics and general merchandise stores in India:

- **Amazon.in**
- **Flipkart**
- **Reliance Digital**

Users can search by typing a product name, pasting a verbose product title (copied from a store), or pasting a full product URL from any of the supported stores. The app extracts the product name intelligently using an AI model, then scrapes all three stores in parallel and presents the results with product images, ratings, and direct buy links.

---

## Features

- **Real-time parallel scraping** — all three stores are scraped simultaneously using `ThreadPoolExecutor`, keeping total response time under 6 seconds
- **AI-powered query cleaning** — messy or verbose product titles (e.g. `"iPhone 17 Pro Max 2 TB: 17.42 cm Display with Promotion, A19 Pro Chip..."`) are automatically cleaned to a minimal search query (`"iPhone 17 Pro Max 2TB"`) using the OpenRouter API
- **URL intelligence** — paste any Amazon, Flipkart, or Reliance product URL directly into the search bar and the app extracts the product name automatically
- **Magic link handler** — paste `yoursite.com/https://www.amazon.in/...` directly in the browser address bar and it works
- **Smart product matching** — a rule-based filter prevents accessories, wrong models, and wrong storage variants from appearing as results
- **Product images** — scrapes and displays the product image from each store
- **Star ratings** — extracted and displayed per store where available
- **Best price highlight** — the cheapest store is highlighted with a trophy banner and animated winner styling
- **TLS fingerprint impersonation** — uses `curl_cffi` to impersonate real Chrome browser TLS handshakes, bypassing bot detection on Amazon and Flipkart
- **CAPTCHA detection & retry** — detects Amazon CAPTCHA pages and retries with a different browser profile
- **Dynamic animated UI** — particle network canvas, 3D card tilt on hover, floating price badges, custom cursor, scroll reveal animations, and animated loading screen

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web framework | Flask 3.1 |
| HTML scraping | BeautifulSoup4 + lxml |
| HTTP requests | requests + curl_cffi |
| TLS impersonation | curl_cffi (Chrome 110–124) |
| Parallel scraping | concurrent.futures ThreadPoolExecutor |
| AI query cleaning | OpenRouter API (google/gemma-3-4b-it:free) |
| Frontend | Vanilla HTML/CSS/JS (Jinja2 templates) |
| Fonts | Orbitron, Space Grotesk (Google Fonts) |
| Animations | CSS keyframes + Canvas API + JS |
| Env management | python-dotenv |
| Production server | Gunicorn |
| Deployment | Hugging Face Spaces (Docker) |

---

## Project Structure

```
bestprice/
├── app.py                  # Flask app — routes, AI cleaning, URL parsing
├── scraper.py              # All scraping logic — Amazon, Flipkart, Reliance
├── requirements.txt        # Python dependencies
├── Dockerfile              # Docker config for Hugging Face Spaces
├── Procfile                # Gunicorn start command
├── .env                    # Local secrets (never commit this)
├── .gitignore
└── templates/
    └── index.html          # Frontend — all HTML, CSS, and JS in one file
```

---

## How It Works

### End-to-end flow

```
User input (text / URL)
        │
        ▼
  app.py — is_url()?
        │
   YES  │  NO
   ┌────┘  └─────────────────────────────────┐
   │                                         │
   ▼                                         ▼
fetch_product_title_from_url()          is it long / messy?
   (fetches page, reads <h1>)                │
        │                              YES   │  NO
        └──────────────┐          ┌──────────┘  └──── use as-is
                       ▼          ▼
                  clean_title_with_ai()
                  (OpenRouter → Gemma 3)
                  "iPhone 17 Pro Max 2TB"
                        │
                        ▼
              get_product_data(query)
             ┌──────────┬──────────┐
             ▼          ▼          ▼
        scrape_      scrape_    scrape_
        amazon()   flipkart()  reliance()
        [thread1]  [thread2]   [thread3]
             └──────────┴──────────┘
                        │
                        ▼
              results dict + best store
                        │
                        ▼
              render index.html
```

---

## Setup & Installation

### Prerequisites

- Python 3.10 or higher
- pip

### 1. Clone the repository

```bash
git clone https://github.com/yourname/bestprice.git
cd bestprice
```

### 2. Create a virtual environment (recommended)

```bash
python3 -m venv venv
source venv/bin/activate        # Mac/Linux
venv\Scripts\activate           # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

> **Note:** `curl_cffi` requires a C compiler on some systems. On Mac, run `xcode-select --install` if installation fails.

### 4. Create your `.env` file

```bash
cp .env.example .env
# then edit .env and add your keys
```

---

## Environment Variables

Create a `.env` file in the project root with the following:

```env
BESTPRICE_OPENROUTER_API=sk-or-xxxxxxxxxxxxxxxx
```

| Variable | Description | Where to get it |
|---|---|---|
| `BESTPRICE_OPENROUTER_API` | OpenRouter API key for AI query cleaning | [openrouter.ai](https://openrouter.ai) — free, no credit card |

> The app works without the OpenRouter key but will not clean verbose product titles — raw titles will be passed directly to the scrapers, which may reduce match accuracy.

---

## Running Locally

```bash
python app.py
```

The app starts on `http://127.0.0.1:80`. If port 80 requires sudo on your system, change it in `app.py`:

```python
app.run(host='0.0.0.0', port=5000)
```

Then access at `http://127.0.0.1:5000`.

---

## Sharing on Local Network

To let anyone on the same WiFi use your app:

**Step 1 — find your local IP**

```bash
# Mac
ipconfig getifaddr en0

# Windows
ipconfig   # look for IPv4 Address under your WiFi adapter

# Linux
hostname -I
```

**Step 2 — make sure app.py binds to all interfaces**

```python
app.run(host='0.0.0.0', port=5000)
```

**Step 3 — share your IP**

Anyone on the same WiFi can now open `http://192.168.x.x:5000` and use the app.

---

## Deployment

### Hugging Face Spaces (recommended — free forever)

1. Create a new Space at [huggingface.co](https://huggingface.co) → **Docker** SDK
2. Add your GitHub repo as a remote:

```bash
git remote add hf https://huggingface.co/spaces/yourname/bestprice
git push hf main
```

3. Go to **Settings → Variables and Secrets** → add `BESTPRICE_OPENROUTER_API`
4. Your app is live at `https://yourname-bestprice.hf.space`

> The `Dockerfile` is already configured for Hugging Face (port 7860, system deps for curl_cffi).

### Pushing updates

```bash
git add .
git commit -m "your changes"
git push origin main    # GitHub
git push hf main        # Hugging Face (auto-redeploys)
```

---

## Scraper Architecture

### Amazon (`scrape_amazon`)

- Uses `curl_cffi` with rotating Chrome impersonation (chrome110–chrome124) to bypass TLS fingerprinting
- Opens a session and hits the homepage first to collect cookies before searching
- Parses `div[data-component-type="s-search-result"]` cards
- Skips sponsored cards first, falls back to sponsored if no organic match found
- Detects CAPTCHA pages (Amazon returns 200 with a block page) and retries with a different Chrome profile
- Extracts: title (`h2 span`), price (`.a-price-whole`), rating (`span.a-icon-alt`), image (`img.s-image`), link (`a[href*="/dp/"]`)

### Flipkart (`scrape_flipkart`)

- Uses `curl_cffi` with `chrome124` impersonation (plain requests gets 403)
- Parses `a[href*="/p/"]` product links
- Extracts title from `img[alt]` or longest non-price div text
- Extracts price via `₹` regex on link text
- Extracts rating from divs matching `^[1-5]\.[0-9]$`
- Extracts image from `img[src]` or `img[data-src]`

### Reliance Digital (`scrape_reliance`)

- Uses their **public JSON API** — no browser needed, no bot detection
- Endpoint: `GET https://www.reliancedigital.in/ext/raven-api/catalog/v1.0/products?q={query}&page_no=1&page_size=24`
- Must set `r.encoding = 'utf-8'` or the ₹ symbol arrives corrupted
- Separately fetches ratings from `/products/{uid}/ratings` endpoint
- Extracts image from `medias[0].url`

### Parallelism

All three scrapers run simultaneously:

```python
with ThreadPoolExecutor(max_workers=3) as executor:
    futures = {
        "Amazon":           executor.submit(scrape_amazon, query),
        "Flipkart":         executor.submit(scrape_flipkart, query),
        "Reliance Digital": executor.submit(scrape_reliance, query),
    }
```

Total time is bounded by the slowest scraper (~4–6s), not the sum of all three.

---

## AI Query Cleaning

When a user inputs a verbose product title like:

```
iPhone 17 Pro Max 2 TB: 17.42 cm (6.9″) Display with Promotion,
A19 Pro Chip, Best Battery Life in Any iPhone Ever, Pro Fusion
Camera System, Center Stage Front Camera; Cosmic Orange
```

The app sends it to the OpenRouter API (Gemma 3 4B, free model) with a few-shot prompt:

```
iPhone 17 Pro Max 2 TB: 17.42 cm Display with Promotion, A19 Pro Chip → iPhone 17 Pro Max 2TB
Samsung Galaxy S25 Ultra 256GB Titanium Black, 200MP Camera → Samsung Galaxy S25 Ultra 256GB
```

The model returns: **`iPhone 17 Pro Max 2TB`**

This is then used as the search query across all three stores.

**Fallback chain:**
1. Try `google/gemma-3-4b-it:free`
2. Try `google/gemma-3-12b-it:free`
3. If all models fail → use first 50 characters of raw input

---

## URL Intelligence

The app handles any of these inputs in the search bar:

| Input type | Example | Handling |
|---|---|---|
| Plain product name | `iPhone 17 Pro 256GB` | Used directly |
| Long product title | `iPhone 17 Pro Max 2 TB: 17.42 cm Display...` | Cleaned via AI |
| Amazon URL (slug) | `amazon.in/Apple-iPhone.../dp/B0F...` | Fetches page, reads `#productTitle`, cleans with AI |
| Amazon ASIN URL | `amazon.in/gp/aw/d/B0CQTNV9H7/...` | Fetches `/dp/{ASIN}`, reads title |
| Flipkart URL | `flipkart.com/apple-iphone.../p/itm...` | Fetches page, reads `h1.yhB1nd` |
| Reliance URL | `reliancedigital.in/product/apple-iphone...` | Fetches page, reads `.pdp-title` |
| Magic link | `yoursite.com/https://amazon.in/...` | Caught by `/<path:full_url>` route |

---

## Product Matching Logic

The `is_correct_product(query, title)` function prevents wrong products from showing up:

### Accessory filter
If the search query doesn't mention accessories, results with accessory keywords (`case`, `cover`, `charger`, `cable`, `glass`, `protector`, etc.) are rejected.

### Brand filter
If the query mentions a specific brand (`iphone`, `samsung`, `oneplus`, etc.), results from other brands are rejected.

### iPhone model exact match
Uses regex to extract and compare iPhone model numbers including suffixes:

```
"iphone 16" ≠ "iphone 16e"
"iphone 16 pro" ≠ "iphone 16 pro max"
"iphone 17 air" ≠ "iphone 17"
```

### Storage variant match
Normalizes storage strings before comparing (`"2 TB"` → `"2tb"`, `"512 GB"` → `"512gb"`):

- If the query specifies storage, results with different storage are rejected
- If the query specifies storage, results with no storage info pass through

---

## Known Limitations

| Issue | Cause | Status |
|---|---|---|
| Amazon intermittent 503/CAPTCHA | Amazon aggressively rate-limits by IP | Partially mitigated with Chrome rotation and retry |
| Flipkart/Reliance blocked on cloud | Both sites geo-block non-Indian datacenter IPs | Use local hosting or an Indian VPS for full functionality |
| Reliance ratings always N/A | Ratings API endpoint not consistently available | Known, low priority |
| No price history | Would require a database and periodic scraping | Not implemented |
| Prices may be slightly stale | Scraped at request time, not cached | By design — always live |

---

## Contributing

Pull requests are welcome. For major changes, open an issue first to discuss what you'd like to change.

### Areas for contribution

- Add more Indian stores (Vijay Sales, Tata Cliq, Croma via API discovery)
- Add price history with SQLite
- Add price drop alerts via email/WhatsApp
- Improve `is_correct_product` for non-phone categories
- Add a loading skeleton UI instead of the full-screen overlay

---

## License

MIT License — free to use, modify, and distribute.

---

*Built by a student, for Indian shoppers. Not affiliated with Amazon, Flipkart, or Reliance Digital.*

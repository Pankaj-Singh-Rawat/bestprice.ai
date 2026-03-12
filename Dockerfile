FROM python:3.11-slim

WORKDIR /app

# Install system dependencies needed by curl_cffi
RUN apt-get update && apt-get install -y \
    gcc \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Hugging Face Spaces uses port 7860
EXPOSE 7860

CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:7860", "--workers", "2", "--timeout", "60"]

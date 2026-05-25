FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY backend/ backend/
COPY frontend/ frontend/
COPY start.py .
COPY desktop.py .

RUN pip install --no-cache-dir -e .

RUN playwright install chromium --with-deps || true

COPY .env.example .env.example

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["python", "start.py", "web"]

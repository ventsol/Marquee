FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first so code changes don't bust the layer cache.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as non-root. /app/data is the mount point for the Railway volume, so the
# unprivileged user has to own it before the volume is attached.
RUN useradd --create-home --uid 1000 marquee \
    && mkdir -p /app/data \
    && chown -R marquee:marquee /app
USER marquee

# Railway injects PORT at runtime; 8000 is the local default.
ENV WEBAPP_HOST=0.0.0.0 \
    WEBAPP_PORT=8000 \
    DATABASE_PATH=data/trailer.db

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request,sys; \
p=os.environ.get('PORT', os.environ.get('WEBAPP_PORT','8000')); \
sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/healthz', timeout=4).status==200 else 1)"

CMD ["python", "app.py"]

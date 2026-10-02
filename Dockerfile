FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first so code changes don't bust the layer cache.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# gosu lets the entrypoint drop from root to the unprivileged user at runtime.
# Required because Railway mounts the volume as root AFTER the image is built,
# so a build-time chown cannot take effect.
# (su-exec is the Alpine equivalent; this is a Debian base, so use gosu.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN useradd --create-home --uid 1000 marquee \
    && mkdir -p /app/data \
    && chmod +x /app/docker-entrypoint.sh \
    && chown -R marquee:marquee /app

# Railway injects PORT at runtime; 8000 is the local default.
ENV WEBAPP_HOST=0.0.0.0 \
    WEBAPP_PORT=8000 \
    DATABASE_PATH=data/trailer.db \
    DATA_DIR=/app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request,sys; \
p=os.environ.get('PORT', os.environ.get('WEBAPP_PORT','8000')); \
sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/healthz', timeout=4).status==200 else 1)"

# Entrypoint starts as root to fix volume ownership, then execs as 'marquee'.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "app.py"]

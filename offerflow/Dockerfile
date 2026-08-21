FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OFFERFLOW_HOST=0.0.0.0 \
    PORT=10000 \
    CHROME_BIN=/usr/bin/chromium \
    BROWSER_MAX_CONCURRENCY=2 \
    RATE_LIMIT_PER_MINUTE=30

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        chromium \
        fonts-noto-cjk \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY index.html server.py README.md ./

RUN useradd --create-home --uid 10001 offerflow \
    && chown -R offerflow:offerflow /app

USER offerflow

EXPOSE 10000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:10000/healthz', timeout=3)" || exit 1

CMD ["python3", "server.py"]

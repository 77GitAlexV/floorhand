FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLOORHAND_DB=/data/floorhand.db \
    FLOORHAND_DEMO=1 \
    FLOORHAND_META_LIVE=0

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 floorhand \
    && useradd --uid 10001 --gid floorhand --no-create-home floorhand \
    && mkdir /data && chown floorhand:floorhand /data
COPY floorhand ./floorhand
COPY deploy ./deploy

USER floorhand
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).close()"
ENTRYPOINT ["sh", "/app/deploy/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "floorhand.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE}
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk ca-certificates tzdata && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd -u 10001 -m app && mkdir /data && chown app:app /data
COPY app ./app
COPY web ./web
COPY scripts ./scripts
ENV DATA_DIR=/data PYTHONUNBUFFERED=1 PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 TZ=Asia/Shanghai
USER app
EXPOSE 18765
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18765/health', timeout=3).read()"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "18765", "--workers", "1", "--timeout-graceful-shutdown", "60"]

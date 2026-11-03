FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd -u 10001 -m app && mkdir /data && chown app:app /data
COPY app ./app
COPY web ./web
COPY scripts ./scripts
ENV DATA_DIR=/data PYTHONUNBUFFERED=1 PYTHONUTF8=1
USER app
EXPOSE 18765
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "18765"]

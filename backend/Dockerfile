FROM python:3.11.18-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/backend/requirements.txt
ARG INSTALL_ASR=false
COPY backend/requirements-asr.txt /app/backend/requirements-asr.txt
RUN if [ "$INSTALL_ASR" = "true" ]; then python -m pip install --no-cache-dir -r /app/backend/requirements-asr.txt; fi

COPY --chown=app:app backend/ /app/backend/
# 新的命名卷沿用目录所有权；数据库与下载文件各自保存。
RUN mkdir -p /app/backend/data /app/backend/downloads /app/backend/models \
    && chown -R app:app /app/backend/data /app/backend/downloads /app/backend/models

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import json, urllib.request; client = urllib.request.build_opener(urllib.request.ProxyHandler({})); response = json.load(client.open('http://127.0.0.1:8000/api/health', timeout=3)); assert response['success'] and response['data']['ffmpeg_available']"

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

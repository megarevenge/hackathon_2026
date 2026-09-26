FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STORAGE_DIR=/app/storage

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libsm6 libxext6 ca-certificates tini gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid appuser --no-create-home appuser \
    && mkdir -p /app/storage && chown appuser:appuser /app/storage

COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY main.py processor.py ./
COPY static ./static
COPY start.sh ./
RUN chmod +x /app/start.sh

# Start as root only to fix the mounted volume root; start.sh drops privileges.
ENTRYPOINT ["/usr/bin/tini", "--", "/app/start.sh"]
CMD ["python", "main.py"]
EXPOSE 8000

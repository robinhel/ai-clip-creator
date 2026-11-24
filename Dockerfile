FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# uid 1000 is the usual first user on Linux, so files in the mounted folders belong to you on the host.
RUN groupadd -r -g 1000 clipsmith && useradd -r -u 1000 -g clipsmith -d /home/clipsmith -m clipsmith

WORKDIR /app

COPY pyproject.toml README.md LICENSE requirements-campaign.txt ./
COPY src/ src/
COPY campaign/ campaign/

# API server, webcam detection and the campaign layer. No cloud or observability extras.
RUN pip install --no-cache-dir -e ".[server,vision]" -r requirements-campaign.txt

# Bake the Whisper model into /app/.cache so the clipsmith user can access it.
# Uses the "small" model matching the default in config.yaml (transcribe.model).
# Rebuild the image if you switch to "medium" or "large-v3".
ENV HF_HOME=/app/.cache/huggingface
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"

# config.*.yaml: the clipsmith settings each campaign in profiles/ points to (clipsmith_config).
COPY alembic.ini config.yaml config.*.yaml priser.yaml ./
COPY alembic/ alembic/
COPY profiles/ profiles/
COPY overlays/ overlays/
COPY stickers/ stickers/
COPY musik/ musik/
COPY scripts/start_server.sh scripts/docker-entrypoint.sh /app/

# KLIPP_ROOT: the campaign layer reads profiles/, priser.yaml, work/, out/ and klippdata/ here.
ENV KLIPP_ROOT=/app

RUN mkdir -p /app/data /app/work /app/out /app/klippdata && \
    chmod +x /app/start_server.sh /app/docker-entrypoint.sh && \
    chown -R clipsmith:clipsmith /app/.cache /home/clipsmith /app/data /app/work /app/out /app/klippdata \
        /app/profiles /app/overlays

VOLUME ["/app/work", "/app/out", "/app/data", "/app/klippdata"]

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# The entrypoint starts as root only to hand fresh mounted folders to clipsmith, then drops to that user.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["/app/start_server.sh"]

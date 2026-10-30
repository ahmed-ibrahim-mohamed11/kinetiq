# Edge AI service image — NVIDIA GPU host (nvidia-container-toolkit required).
# Build from the repo root:
#   docker build -f infrastructure/docker/edge-ai.Dockerfile -t smartgym/edge-ai:0.1.0 .
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04 AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg provides the RTSP/H.264/H.265 demux+decode backend for OpenCV.
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3.10 python3.10-venv python3-pip ffmpeg libgl1 libglib2.0-0 tini \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 edge
WORKDIR /app

COPY services/edge-ai/pyproject.toml services/edge-ai/README.md ./
COPY services/edge-ai/edge_ai ./edge_ai
COPY packages/api-contracts/fixtures ./contract-fixtures

RUN python3.10 -m venv /opt/venv && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install ".[cv]"
ENV PATH="/opt/venv/bin:$PATH"

# Runtime dirs: data (sqlite queue + encrypted templates), config (cameras.yaml, credentials), models.
RUN mkdir -p /data /config /models && chown -R edge:edge /data /config /models /app
VOLUME ["/data", "/config", "/models"]
USER edge

ENV EDGE_DATA_DIR=/data \
    EDGE_CAMERAS_FILE=/config/cameras.yaml \
    EDGE_CAMERA_SECRETS_FILE=/config/camera-credentials.yaml \
    EDGE_HEALTH_PORT=8088

EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python3.10 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8088/healthz', timeout=3).status in (200,503) else 1)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["edge-ai", "run", "--detector", "/models/detector.onnx"]

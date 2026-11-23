# GYM-AI local analysis service (Mode A, CPU).
#   docker build -t gym-ai .
#   docker run --rm -p 127.0.0.1:8000:8000 gym-ai        # keep it on localhost: no authentication
# NOT YET BUILT: Docker is not installed on the development machine (docs/REPRODUCIBILITY.md).
# Once built, pin the base image by digest (FROM python:3.10-slim-bookworm@sha256:...).
FROM python:3.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# opencv-python needs libGL and glib at import time
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements/ requirements/
# CPU build of the pinned torch version first, WITHOUT dependencies (the PyTorch index serves some
# of them only as unbuildable sdists); requirements/ then supplies every dependency from PyPI.
RUN pip install --no-deps --index-url https://download.pytorch.org/whl/cpu torch==2.14.0 torchvision==0.29.0 \
 && pip install -r requirements/service.txt \
 && pip check

COPY . .
RUN python -m ai.scripts.download_checkpoints \
 && useradd --create-home --uid 10001 app \
 && chown -R app /app

USER app
# No Ultralytics network calls at runtime (telemetry, auto-downloads); see docs/PRIVACY.md
ENV GYM_AI_DEVICE=cpu \
    YOLO_OFFLINE=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s \
  CMD python -c "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/v1/health').status != 200)"
CMD ["python", "-m", "ai.service", "--host", "0.0.0.0", "--port", "8000"]

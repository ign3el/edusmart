# Backend Dockerfile (ARM-friendly)
FROM python:3.11-slim-bookworm

ENV PIP_ROOT_USER_ACTION=ignore
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System dependencies required for various packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    build-essential \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt ./
RUN pip install --upgrade pip setuptools && pip install -r requirements.txt

# Verify imports
RUN python -c "from pypdf import PdfReader; import docx; from langdetect import detect; print('All specified modules imported successfully during build.')"

# Stamped by deploy.sh with the image tag, so /api/health can report exactly
# which build is live. Placed after the pip layers so it never busts that cache.
ARG BUILD_TAG=dev
ENV BUILD_TAG=${BUILD_TAG}

# Copy application code
COPY . .

# Every directory the app writes to. These are all mount points in
# docker-compose (bind mounts for media, a named volume for db_data), but they
# must exist in the image too so a bare `docker run` of this image still works
# and so the mount targets have the right ownership.
RUN mkdir -p uploads outputs outputs/audio_cache outputs/status \
        saved_stories generated_stories db_data \
    && chown -R 1001:1001 /app

EXPOSE 8000

# --proxy-headers makes uvicorn trust X-Forwarded-For from its immediate peer
# and rewrite request.client.host accordingly (used by the login rate limiter).
# Safe to trust unconditionally here because the port is now bound to
# 127.0.0.1 only (see docker-compose.yml) - nginx is the only possible peer.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]

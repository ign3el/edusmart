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

# Copy application code
COPY . .

# Create necessary directories at runtime (handled in main.py)
# but we can ensure the base 'outputs' exists
RUN mkdir -p uploads outputs saved_stories

EXPOSE 8000

# --proxy-headers makes uvicorn trust X-Forwarded-For from its immediate peer
# and rewrite request.client.host accordingly (used by the login rate limiter).
# Safe to trust unconditionally here because the port is now bound to
# 127.0.0.1 only (see docker-compose.yml) - nginx is the only possible peer.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]

# Use an official lightweight Python image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create a non-root user for security
RUN useradd --create-home --shell /bin/bash app \
    && chown -R app:app /app
USER app

# Install Python dependencies before copying any app code or model files.
# This layer is only invalidated when requirements.txt changes, not when
# source files or the model are updated.
COPY requirements.txt .
RUN --mount=type=cache,target=/home/app/.cache/pip \
    pip install -r requirements.txt

# Copy application code (changes frequently — after pip so edits don't
# bust the pip layer)
COPY --chown=app:app new_recorder.py .
COPY --chown=app:app webserver.py .
COPY --chown=app:app templates templates
COPY --chown=app:app appicon.png .
COPY --chown=app:app config config

# NOTE: Whisper model (large-v3.pt) is now mounted as a volume at runtime
# instead of copied into the image. This reduces the build image size from
# 6+ GB to ~1.5 GB and prevents disk exhaustion during docker compose build.

# Create directories for recordings and transcriptions
RUN mkdir -p /app/recordings /app/transcriptions

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:5003/health', timeout=5)" || exit 1

# Default command
CMD ["python", "new_recorder.py"]

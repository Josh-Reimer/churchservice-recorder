# ============================================================================
# STAGE 1: Builder — compiles all Python dependencies once and caches them
# ============================================================================
FROM python:3.11-slim as builder

# Install build tools needed for compiling Python packages from source
# (e.g., openai-whisper, numpy, torch extensions)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    make \
    git \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create a virtual environment to isolate built packages
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV VIRTUAL_ENV=/opt/venv

# Copy requirements and install with BuildKit cache mount
# This layer is cached until requirements.txt changes
WORKDIR /tmp
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install -r requirements.txt

# ============================================================================
# STAGE 2: Runtime — copies pre-built packages from builder, no compilation
# ============================================================================
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Copy the pre-built virtual environment from builder (avoids recompilation)
COPY --from=builder /opt/venv /opt/venv

# Activate virtual environment
ENV PATH="/opt/venv/bin:$PATH"
ENV VIRTUAL_ENV=/opt/venv

# Install only runtime dependencies (no build tools, no compilation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create a non-root user for security
RUN useradd --create-home --shell /bin/bash app \
    && chown -R app:app /app
USER app

# Set working directory
WORKDIR /app

# Copy application code (changes frequently — after deps so code edits don't
# bust the pip layer, which is now in the venv from builder)
COPY --chown=app:app new_recorder.py .
COPY --chown=app:app webserver.py .
COPY --chown=app:app templates templates
COPY --chown=app:app appicon.png .
COPY --chown=app:app config config

# Create directories for recordings and transcriptions
RUN mkdir -p /app/recordings /app/transcriptions

# NOTE: Whisper model (large-v3.pt) is mounted as a volume at runtime
# instead of copied into the image. This reduces build requirements and
# allows the model to be shared between containers.

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:5003/health', timeout=5)" || exit 1

# Default command
CMD ["python", "new_recorder.py"]

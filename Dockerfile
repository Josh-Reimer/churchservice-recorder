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

COPY models models

# Copy requirements file first (for better Docker layer caching)
COPY requirements.txt .

COPY --chown=app:app webserver.py .
COPY --chown=app:app templates templates
COPY appicon.png .

# Install Python dependencies
RUN pip install --user --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=app:app new_recorder.py .
COPY config config
# Create directories for recordings and transcriptions
RUN mkdir -p /app/recordings /app/transcriptions

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8099/health', timeout=5)" || exit 1

# Default command
CMD ["python", "new_recorder.py"]
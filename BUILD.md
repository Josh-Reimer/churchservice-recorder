# Building and Running the Church Service Recorder

## Quick Start

```bash
# 1. Set up environment variables
cp .env.example .env
# Edit .env with your TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TIMEZONE, etc.

# 2. Download the Whisper model (one-time, ~2.9 GB, takes 5-10 minutes)
python download_model.py

# 3. Build and start containers with BuildKit enabled (faster, uses less disk)
export DOCKER_BUILDKIT=1
docker compose up -d --build

# 4. Access the web UI
# Open http://0.0.0.0:5003 in your browser
# Login: admin / 42
```

## Architecture: Multi-Stage Build + Volume Mount

**Problem:** Docker build was crashing during pip install when compiling large packages like openai-whisper, torch, and torchaudio from source. This consumed all available RAM and CPU on the build system.

**Solution:** Two-part approach:

### 1. Multi-Stage Dockerfile (Prevents Recompilation)
```
Stage 1 (Builder):     Installs build tools, compiles dependencies → cached
                       ↓
Stage 2 (Runtime):     Copies pre-built packages from builder, no compilation
```

**Benefits:**
- ✅ Heavy compilation happens **once and is cached in builder layer**
- ✅ Subsequent rebuilds just copy pre-built packages (~30 seconds)
- ✅ Prevents OOM crashes during pip install
- ✅ Builder stage size doesn't affect final image (layer is discarded)

### 2. Whisper Model as Volume Mount (Reduces Image Size)
The 2.9 GB Whisper model is mounted at runtime, not bundled in the image.

**Benefits:**
- ✅ Docker image reduced from 6+ GB to ~1.5 GB
- ✅ Model downloaded once to host, shared by containers
- ✅ Build disk requirements: ~4 GB instead of 12 GB

**Combined Result:**
- Build is now safe from OOM crashes
- Rebuilds are **10-20x faster** after first build (cached builder layer)
- Total disk required is minimal

## How It Works

1. **download_model.py** uses the `whisper` Python library to download the model to `./models/large-v3.pt`
2. **docker-compose.yml** mounts `./models:/app/models:ro` (read-only)
3. **new_recorder.py** loads the model from `/app/models/large-v3.pt` at startup
4. Both `recorder` and `webserver` containers can access the model

## Storage Requirements

| Component | Size | Location |
|-----------|------|----------|
| Docker image | ~1.5 GB | Docker registry |
| Whisper model | ~2.9 GB | `./models/large-v3.pt` (host) |
| Recordings | Variable | `./recordings/` (host) |
| Transcriptions | Variable | `./transcriptions/` (host) |
| Config | < 1 MB | `./config/streams.yml` (host) |
| **Total (minimal)** | **~4.4 GB** | Host + Docker |

**Requirement:** Your system should have at least 5 GB free disk space.

## BuildKit (Recommended for Faster Builds)

Docker BuildKit is faster and smarter about caching:

```bash
# Enable for this session
export DOCKER_BUILDKIT=1

# Or enable permanently (Linux)
echo '{"features":{"buildkit":true}}' | sudo tee /etc/docker/daemon.json

# Then rebuild
docker compose up -d --build
```

BuildKit reduces build time by ~30% and uses less intermediate disk space.

## Troubleshooting

### "Model not found" error when running containers

**Symptom:** Container starts but fails with "No such file or directory: /app/models/large-v3.pt"

**Solution:** Download the model first:
```bash
python download_model.py
```

Verify the model file exists:
```bash
ls -lh models/large-v3.pt
```

### "Not enough space" during build

**Symptom:** Docker build fails with "No space left on device"

**Solutions:**
1. Clean docker cache:
   ```bash
   docker system prune -a
   ```

2. Free up disk space (aim for 5+ GB free):
   ```bash
   df -h
   ```

3. Build on a different disk (if available):
   ```bash
   sudo dockerd --data-root /mnt/large-disk/docker &
   ```

### "permission denied" when mounting model

**Symptom:** Container exits with permission error on `/app/models`

**Solution:** Check file permissions:
```bash
ls -ld models/
# Should be readable by the container user (uid 1000, gid 1000)
# If not, fix permissions:
chmod 755 models/
chmod 644 models/*.pt
```

## Manual Model Setup (If download_model.py Fails)

If the Python script doesn't work, you can download manually:

```bash
# Option A: Use Hugging Face (requires git-lfs)
git clone https://huggingface.co/openai/whisper-large-v3 models/

# Option B: Download directly from OpenAI
mkdir -p models
curl -L https://openaipublic.blob.core.windows.net/main/whisper/models/e5b1a6b353c9b0cb90c51f46674bac088a89ebe99d1fbba1014191919db51ded/large-v3.pt \
  -o models/large-v3.pt

# Option C: Use whisper CLI if installed
whisper --model large-v3 --help  # This downloads the model
```

## Container Logs

```bash
# View recorder logs
docker logs -f church-recorder

# View webserver logs
docker logs -f church-webserver

# View app-specific logs (written to disk, not stdout)
docker exec church-recorder tail -f app.log
```

## Rebuilding After Code Changes

```bash
# Rebuild recorder only (fast, no model copy)
export DOCKER_BUILDKIT=1
docker compose up -d --build recorder

# Rebuild webserver only
docker compose up -d --build webserver

# Rebuild both
docker compose up -d --build
```

## Environment Variables

Create or edit `.env`:

```bash
# Required
TIMEZONE=America/St_Johns
TELEGRAM_BOT_TOKEN=your_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# Optional
LOG_LEVEL=INFO
STREAM_URL=https://...  # Legacy single-stream URL
STREAM_STATUS_URL=https://...  # Stream status API endpoint
```

See `CLAUDE.md` for detailed configuration options.

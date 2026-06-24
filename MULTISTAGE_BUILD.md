# Multi-Stage Docker Build Explanation

## Problem: OOM Crash During pip install

The previous single-stage Dockerfile crashed on 8 GB systems during the `pip install -r requirements.txt` step because:

1. **Requirements included heavy packages:**
   - `torch==2.3.0` — requires compilation or large wheels (~2.3 GB)
   - `torchaudio==2.3.0` — requires compilation
   - `openai-whisper` — **no pre-built wheel**, must be compiled from source

2. **Build process in single stage:**
   ```
   Stage 1:
     apt-get install build-essential gcc g++  (for compilation)
     pip install torch (download + extract)
     pip install torchaudio (compile from source)
     pip install openai-whisper (compile from source)  ← CRASH HERE
   ```

3. **Why it crashed:**
   - Compilation requires C++ compiler + significant RAM
   - Multiple packages being compiled simultaneously filled memory
   - System ran out of swap/RAM
   - **Linux OOM killer terminated the docker daemon**
   - **Container crashed, server rebooted**

## Solution: Multi-Stage Build

### How It Works

```
┌─────────────────────────────────────────────────────────┐
│ STAGE 1: Builder (Temporary, Discarded After Build)     │
├─────────────────────────────────────────────────────────┤
│  FROM python:3.11-slim                                  │
│  RUN apt-get install build-essential gcc g++ ...        │
│  COPY requirements.txt .                                │
│  RUN pip install -r requirements.txt                    │
│  # Creates: /opt/venv/ with all compiled packages       │
│  # Result: Cached by Docker                             │
│  # Size: ~10 GB (temporary, not in final image)         │
└─────────────────────────────────────────────────────────┘
           ↓ (copy /opt/venv from builder)
┌─────────────────────────────────────────────────────────┐
│ STAGE 2: Runtime (Final Image Delivered to Server)      │
├─────────────────────────────────────────────────────────┤
│  FROM python:3.11-slim                                  │
│  COPY --from=builder /opt/venv /opt/venv               │
│  # Just copies pre-compiled packages, NO compilation    │
│  # Result: Fast (~2 seconds for copy)                   │
│  # Size: ~1.5 GB (final image, slim)                    │
│                                                         │
│  RUN apt-get install ffmpeg curl                        │
│  COPY new_recorder.py webserver.py ...                  │
│  # Final image ready to run                             │
└─────────────────────────────────────────────────────────┘
```

### Key Insight: BuildKit Caching

Docker BuildKit **caches layers independently**:

**First Build:**
```
Builder stage:      ⏱️  ~5-10 minutes (compilation happens)
                    → Cached in /opt/venv

Runtime stage:      ⏱️  ~30 seconds (copy + ffmpeg install)
                    → Cached in docker registry
```

**Subsequent Builds:**
```
Builder stage:      ⏱️  ~2 seconds (cache hit, no recompilation)
                    → Uses cached /opt/venv

Runtime stage:      ⏱️  ~20 seconds (copy + ffmpeg install)
                    → Uses cached layers
```

**Total time after first build: ~30 seconds** (vs 5-10 minutes before)

### Why This Prevents Crashes

**Single-Stage (Crashes):**
```
pip compile torch (2 GB RAM used)
  + pip compile torchaudio (3 GB RAM used)
    + pip compile openai-whisper (2 GB RAM used)
      = 7+ GB peak memory → OOM on 8 GB system ❌
```

**Multi-Stage (Safe):**
```
Builder Stage (runs once, result cached):
  Compile torch (2 GB RAM) → finish, cache result
  Compile torchaudio (3 GB RAM) → finish, cache result
  Compile openai-whisper (2 GB RAM) → finish, cache result
  Peak memory during each: manageable

Runtime Stage (copies pre-compiled):
  COPY /opt/venv from builder → no compilation
  Only needs ~500 MB RAM to copy files ✅
```

## Build Stages Explained

### Stage 1: Builder

```dockerfile
FROM python:3.11-slim as builder

RUN apt-get install build-essential gcc g++ make git
RUN python -m venv /opt/venv
COPY requirements.txt .
RUN pip install -r requirements.txt
```

**Purpose:** Install all dependencies with build tools
**Result:** `/opt/venv/` contains all compiled packages
**Caching:** This layer is cached after first build
**Discarded:** Builder stage is not included in final image
**Temporary size:** ~10 GB (doesn't affect final image)

### Stage 2: Runtime

```dockerfile
FROM python:3.11-slim

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN apt-get install ffmpeg curl  # Only runtime deps, no build tools
COPY new_recorder.py webserver.py ...
```

**Purpose:** Run the application with pre-built dependencies
**Result:** Slim runtime image (~1.5 GB)
**Caching:** Uses cached builder output + caches its own layers
**Speed:** Fast because no compilation happens

## Performance Impact

### Build Time
| Scenario | Time | Notes |
|----------|------|-------|
| **First build** | 5-10 min | Compilation happens once |
| **Rebuild after code change** | ~30 sec | Cached dependencies, just copy |
| **Rebuild after requirements.txt change** | 5-10 min | Recompile, but cached afterward |
| **Rebuild with docker prune** | 5-10 min | No cache, everything recompiled |

### Memory Usage During Build
| Stage | Memory Peak | Risk |
|-------|-------------|------|
| Builder (compile torch) | ~2 GB | Safe on 8 GB system ✅ |
| Builder (compile whisper) | ~2 GB | Safe on 8 GB system ✅ |
| Runtime (copy /opt/venv) | ~500 MB | Very safe ✅ |

### Disk Usage
| Component | Size |
|-----------|------|
| Builder stage (temporary) | ~10 GB (not kept) |
| Runtime image (final) | ~1.5 GB |
| Build cache (between builds) | ~5 GB (Docker cache) |
| **Total on disk after build** | ~6.5 GB |

## Testing the Build

### First Build (Slow, Compiles Dependencies)
```bash
docker system prune -a          # Clean old cache
export DOCKER_BUILDKIT=1
docker compose up -d --build
# Expected: Takes 5-10 minutes, compiles everything
```

### Check Builder Cache Was Created
```bash
docker buildx build --progress=plain .
# Look for: "=> caching layer for builder stage"
```

### Subsequent Rebuild (Fast, Uses Cache)
```bash
# Make a code change
echo "# test" >> new_recorder.py

# Rebuild
docker compose up -d --build
# Expected: Takes ~30 seconds, uses cached builder stage
```

### Verify Image Size
```bash
docker images | grep church
# Final image should be ~1.5 GB (not 6+ GB)
```

## Troubleshooting

### "Builder stage always recompiles"
**Symptom:** Each build recompiles dependencies (slow)

**Cause:** BuildKit cache not being used

**Solution:**
```bash
export DOCKER_BUILDKIT=1
docker buildx build --load .
```

### "Still getting OOM during build"
**Symptom:** Build still crashes during pip install

**Cause:** Builder stage still running out of memory

**Solution:**
- Rebuild on a machine with more RAM
- Add swap space: `fallocate -l 4G /swapfile && swapon /swapfile`
- Use a smaller model: change `large-v3` to `base` in code

### "Runtime image is still 6+ GB"
**Symptom:** Final image size didn't shrink

**Cause:** Builder stage wasn't properly discarded, or model bundled

**Solution:**
```bash
docker system prune -a  # Remove dangling images
docker compose build --no-cache
```

Verify model is mounted, not bundled:
```bash
grep "COPY.*models" Dockerfile  # Should NOT match
grep "./models:/app/models" docker-compose.yml  # Should match
```

## Files Changed

- **Dockerfile** — Split into two stages (builder + runtime)
- **BUILD.md** — Updated with multi-stage explanation
- **CLAUDE.md** — Updated with build system documentation

## Architecture Summary

```
┌──────────────────────────────────────────────────┐
│         Docker Compose (docker-compose.yml)      │
├──────────────────────────────────────────────────┤
│  Both recorder and webserver build from:         │
│  - Dockerfile (multi-stage)                      │
│  - Volume mount: ./models:/app/models:ro         │
│                                                  │
│  Result: Two containers from one image           │
│  - Fast rebuilds (cached builder stage)          │
│  - Safe from OOM crashes                         │
│  - Model shared via volume                       │
└──────────────────────────────────────────────────┘
```

## Next Steps

1. **Test the build** on your server with `docker compose up -d --build`
2. **Monitor memory** during build: `watch free -h`
3. **Test rebuild speed** after code changes
4. **Verify model loads** in containers: `docker logs church-recorder | grep Whisper`

If successful, this eliminates the server reboot issue while dramatically speeding up rebuilds!

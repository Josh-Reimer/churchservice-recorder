# PyTorch CPU vs CUDA Configuration

## Current Setup: CPU-Only (Default)

The `requirements.txt` now specifies **CPU-only PyTorch** to prevent OOM crashes during Docker build.

```
torch==2.3.0+cpu      # ~130 MB (vs 2.3 GB with CUDA)
torchaudio==2.3.0+cpu # ~50 MB
```

### Why CPU-Only?

On 8 GB systems, downloading and extracting CUDA wheels exhausted RAM:
- `torch-2.3.0` with CUDA: 2.3 GB
- `nvidia_cublas_cu12`: 410 MB
- `nvidia_cudnn_cu12`: 731 MB
- **Total: 4+ GB of wheels extracted simultaneously → OOM**

CPU-only wheels are **much smaller** (~130 MB) and don't include CUDA libraries.

### How It Still Works

The `new_recorder.py` code is GPU-aware:

```python
device = "cuda" if torch.cuda.is_available() else "cpu"
model = whisper.load_model(model_name_or_path, device=device)
```

**With CPU-only torch installed:**
- ✅ Whisper transcription works on CPU
- ✅ Build succeeds without OOM
- ✅ Fast enough for church recordings (30-45 min files)
- ✅ No CUDA libraries needed during build

### Performance Impact

| Metric | CPU-Only | With CUDA |
|--------|----------|-----------|
| Build image size | ~1.5 GB | ~3 GB |
| Build time | ~6-10 min | ~6-10 min |
| Transcription speed | Slower | Faster (2-5x) |
| Build safety | ✅ Safe | ❌ OOM risk |

**For 30-45 min church recordings:** CPU transcription takes ~30-60 min (reasonable for background job)

## Switching Back to CUDA (Optional)

If you have a GPU and want faster transcription, revert to CUDA wheels:

### Edit requirements.txt

**REMOVE:**
```
--find-links https://download.pytorch.org/whl/cpu/torch_stable.html
torch==2.3.0+cpu
torchaudio==2.3.0+cpu
```

**REPLACE WITH:**
```
torch==2.3.0
torchaudio==2.3.0
```

### Add CUDA Libraries to Dockerfile

In `Dockerfile`, add CUDA runtime to the builder stage:

```dockerfile
FROM python:3.11-slim as builder

# Install CUDA runtime + build tools
RUN apt-get install -y --no-install-recommends \
    build-essential gcc g++ make git \
    cuda-toolkit-12-1 cuda-runtime-12-1 \  # ← Add this
    libcudnn8 libcudnn8-dev  # ← Add this
```

Then rebuild:
```bash
export DOCKER_BUILDKIT=1
docker system prune -a
sudo docker compose up -d --build
```

**Warning:** This will add 5-10 GB to the build and may cause OOM again on 8 GB systems. Only use if:
- You have a GPU in the server
- You have 16+ GB RAM or added swap space
- You need faster transcription

## Checking Which Torch Is Running

After deployment, verify which version is active:

```bash
docker exec church-recorder python -c \
  "import torch; print(f'CUDA available: {torch.cuda.is_available()}')"
```

**Output:**
- `CUDA available: False` → CPU-only torch (current)
- `CUDA available: True` → CUDA torch with GPU

## Performance Expectations

### CPU-Only (Current)

```
30-minute recording → ~30-60 minutes to transcribe (background job)
45-minute recording → ~45-90 minutes to transcribe

Runs in background while recording continues
User can check completed transcriptions in web UI
```

### With CUDA GPU

```
30-minute recording → ~3-5 minutes to transcribe (2-10x faster)
45-minute recording → ~5-10 minutes to transcribe
```

**For church services:** CPU is fine since transcription is async and runs overnight.

## Troubleshooting

### "CUDA available: False" but CUDA installed?

Torch may not be using CUDA. Check:

```bash
docker exec church-recorder python -c "import torch; print(torch.cuda.get_device_name(0))"
# Should print GPU name, or error if not available
```

### Still getting OOM with CPU-only?

If CPU-only still crashes during pip install:

1. **Add swap space:**
   ```bash
   sudo fallocate -l 4G /swapfile
   sudo swapon /swapfile
   ```

2. **Or use even smaller torch version:**
   Edit requirements.txt:
   ```
   torch==2.0.0+cpu  # Smaller than 2.3.0
   torchaudio==2.0.0+cpu
   ```

3. **Or pre-download torch:**
   ```bash
   pip download torch==2.3.0+cpu torchaudio==2.3.0+cpu
   # Then manually copy wheels into build cache
   ```

## Summary

- **Current (CPU-only):** Safe on 8 GB, works fine for church recordings
- **CUDA (optional):** 2-10x faster transcription, requires 16+ GB RAM or GPU
- **Decision:** Use CPU-only for reliability, switch to CUDA if performance becomes an issue

See `requirements.txt` for current configuration.

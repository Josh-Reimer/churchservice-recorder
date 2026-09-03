# CLAUDE.md
always ask the user what their intened behavious is and confirm it with them before making changes.
run tests before building the docker containers and keep in mind that the containers will run on debian based linux distrubutions.
after building docker containers, always check the logs for any errors. suggest to the user to fix cosemtic errors in logs if you see any. if you find major critical errors, create a plan first and then ask the user for confirmation.
this application should be able to run on less than 8gb of ram and use no more than 3 gb of storage minus the ai model files. it should be able to perform well on an intel core i3 cpu.

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

ByteWorship Recorder automatically records Icecast audio streams (from listentochurch.com) on a configurable schedule, then transcribes them with OpenAI Whisper locally. It runs as two Docker containers: one for recording/transcription (`new_recorder.py`) and one for a Flask web UI (`webserver.py`).

## Running the project

**Important: Quick Start**

```bash
# 1. Download the Whisper large-v3 model (~2.9 GB, takes 5-10 minutes)
python download_model.py

# 2. Build and start both containers (CPU-only torch by default)
export DOCKER_BUILDKIT=1
docker compose up -d --build

# 3. Access the web UI: http://0.0.0.0:5003
# Login: admin / 42
```

**Note:** Default build uses CPU-only PyTorch (~130 MB wheels) to prevent OOM crashes on 8 GB systems. Whisper transcription still works fine on CPU (30-45 min recordings take 30-90 min to transcribe in background). See `TORCH_CPU_VS_CUDA.md` if you want CUDA/GPU support instead.

**Troubleshooting common build issues:**

- If you see "No space left on device" during build: `docker system prune -a` to free up disk
- If model download fails: `python download_model.py` to retry, or see BUILD.md for manual setup
- If containers can't find the model: Verify `ls -lh models/large-v3.pt` exists

**Shell access and logs:**
```bash
# Shell into a container
docker exec -it church-recorder bash
docker exec -it church-webserver bash

# Recorder logs go to app.log (file handler only, not stdout)
docker exec church-recorder tail -f app.log
```

**Testing outside Docker:**
```bash
python record_now.py        # records until Ctrl+C
python record_now.py 60     # records for 60 seconds
```

**See BUILD.md for detailed build instructions and troubleshooting.**

## Required `.env` file

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TIMEZONE=America/St_Johns   # container's local timezone (affects scheduling)
LOG_LEVEL=INFO
STREAM_URL=...              # legacy single-stream; use streams.yml for multi-stream
STREAM_STATUS_URL=...
```

## Architecture

### Two containers, one image

Both `recorder` and `webserver` build from the same `Dockerfile` but run different entry points. They share three volumes:

| Volume | Purpose |
|--------|---------|
| `./recordings` | MP3 output, organized by stream |
| `./transcriptions` | Whisper `.txt` output |
| `./config` | Live `streams.yml` — editable at runtime |

The `./config` volume is writable by both containers so the web UI can edit stream config and the recorder picks up changes without a restart.

### `new_recorder.py` — recorder

**Data model:**

- `StreamInfo` — one per stream. Holds URL, timezone, output dirs, `slots: list[ServiceSlot]`, and `enabled` flag.
- `ServiceSlot` — one per scheduled recording window within a stream. Holds `day_of_week` (int, 0=Mon…6=Sun), `morning_time_text`, `evening_time_text`, converted `morning_time`/`evening_time` datetimes (in container local tz), `sunday_school_break`, and `last_fired` dict.

**Boot sequence:**

1. Reads `config/streams.yml`, calls `build_services()` → populates `services: list[StreamInfo]`
2. Starts `gpu_transcribe_worker` background thread. It checks free RAM before loading the model — see **RAM-gated transcription** below.
3. Schedules `check_services()`, `check_config_reload()` every `CHECK_INTERVAL` (30 s), and `check_transcription_worker()` every `RAM_RECHECK_INTERVAL_MINUTES` (20 min)

**Schedule loop:**

`check_services()` iterates every `StreamInfo` → every `ServiceSlot`. For each slot where today's weekday (in the stream's own timezone) matches `slot.day_of_week`, it calls `maybe_start_service()` for morning and evening. `maybe_start_service()` fires `record_stream()` in a thread pool within `SERVICE_TRIGGER_WINDOW` (60 s), using `slot.last_fired` to prevent double-firing.

**Config hot-reload:**

`check_config_reload()` compares `streams.yml` mtime every 30 s. On change it calls `build_services()` and replaces the `services` list, preserving per-slot `last_fired` state so in-progress scheduling isn't disrupted.

**Recording flow:**

`record_stream()` polls `status_url` until the stream goes live, then runs ffmpeg. It monitors continuously and stops after `MAX_OFFLINE_POLLS` (3) consecutive offline checks. If `sunday_school_break=true`, it waits for the stream to return and records a second segment. Finished recordings are pushed to a `queue.Queue` for transcription.

**Backwards compatibility:**

Legacy `sunday_morning_service_time` / `sunday_evening_service_time` fields are auto-migrated to a single Sunday `ServiceSlot` on read — old YAML works without changes.

**RAM-gated transcription:**

Whisper `large-v3` needs ~6 GB just to load on CPU (fp16 checkpoint upcast to fp32) — verified by OOM-killing the recorder container at both 6 GB and 8 GB memory limits; it only survived at 10 GB. Since this exceeds the project's <8 GB target, `gpu_transcribe_worker()` checks `psutil.virtual_memory().available` against `MIN_TRANSCRIBE_RAM_GB` (default 7) before loading the model. Recording is never gated on this — only transcription is.

- If RAM is insufficient, the worker thread exits without loading the model. Queued files stay in `transcription_queue` untouched. A WARNING is logged, and a one-time Telegram notice is sent (`_low_ram_notified` guards against repeat spam).
- `check_transcription_worker()` runs every `RAM_RECHECK_INTERVAL_MINUTES` and restarts the worker if it isn't alive — so transcription resumes automatically once RAM frees up, without a container restart. `schedule_transcriptions()` is idempotent (guarded by `_worker_start_lock`) so this can't race with the reactive restart in `queue_transcription()`.
- This check reads host-wide available memory via `psutil`, not a cgroup limit. It only reflects reality if the container has **no** `mem_limit`/`deploy.resources.limits` set in `docker-compose.yml` — which is the current, intentional setup. Adding a hard memory cap smaller than the host would let the kernel OOM-kill the container before this check ever sees the pressure.

### `webserver.py` — Flask UI

Session-based login (`admin`/`42`, hardcoded). CSRF via `flask-wtf`.

| Route | Purpose |
|-------|---------|
| `/index` | Recordings list with audio player and transcriptions |
| `/config` | Stream config dashboard |
| `/config/stream/add` | POST: add stream |
| `/config/stream/<idx>/edit` | POST: replace stream entry |
| `/config/stream/<idx>/toggle` | POST: flip `enabled` flag |
| `/config/stream/<idx>/delete` | POST: remove stream |

The config dashboard renders service slots dynamically. Edit/add forms POST indexed fields (`slot_day_0`, `slot_morning_0`, `slot_evening_0`, `slot_ssb_0`, `slot_count`) parsed by `_parse_slots_from_form()`. Legacy streams are normalized via `_normalize_stream()` before being passed to the template.

### `config/streams.yml` schema

New format (written by UI):
```yaml
streams:
  - name: short_id
    full_name: Human Readable Name      # drives output subdirectory name
    url: https://...stream.mp3
    status_url: https://...api/status   # must return JSON {"status": 1} when live
    timezone: America/Chicago
    enabled: true
    services:
      - day: sunday
        morning: "10:00"
        evening: "18:00"
        sunday_school_break: true       # records second segment after morning ends
      - day: wednesday
        evening: "19:00"
```

Legacy format (still accepted):
```yaml
  - name: short_id
    timezone: America/Chicago
    sunday_morning_service_time: "10:00"
    sunday_evening_service_time: "18:00"
    sunday_school_break: false
```

Output dirs are derived from `full_name`: lowercased, spaces→`_`, `cong`→`congregation`, commas/dots stripped.

### Key constants (`new_recorder.py`)

| Name | Default | Purpose |
|------|---------|---------|
| `CHECK_INTERVAL` | 30 s | Poll interval for status checks and config reload |
| `CHECK_TIMEOUT` | 90 min | Give up waiting for a stream to come online |
| `MAX_OFFLINE_POLLS` | 3 | Consecutive offline polls before stopping recording |
| `SERVICE_TRIGGER_WINDOW` | 60 s | How late a scheduled trigger can still fire |
| `MIN_TRANSCRIBE_RAM_GB` | 7 GB (env `MIN_TRANSCRIBE_RAM_GB`) | Minimum free RAM required to load Whisper; below this, transcription is skipped, not recording |
| `RAM_RECHECK_INTERVAL_MINUTES` | 20 min (env `RAM_RECHECK_INTERVAL_MINUTES`) | How often to retry starting the transcription worker after a low-RAM skip |
| `MAX_CONCURRENT_RECORDINGS` | `os.cpu_count()`, min 2 (env `MAX_CONCURRENT_RECORDINGS`) | Concurrent ffmpeg recording jobs allowed; scales to the host instead of a fixed guess |

### Utility scripts

- `record_now.py` — one-shot manual recording against a hardcoded local stream URL
- `local_transcribe.py` / `sermon_to_text.py` — standalone Whisper transcription of existing files
- `audio_length.py` — prints duration of an audio file


### Timezones
This application requires great care in timezone accuracy. If timezones are not accurate on the server, streams could be recorded at the wrong time or not at all. every time you run a code review, develop a sprint plan, or push to github, check over the timezone related logic to make sure nothing got missed or accidently changed. This is worth spending time on.
For example, if the stream is in America/Denver time, and the server is on America/Vancouver time, remember to subtract the number of hours different that those timezones are, from the scheduler logic so the recording function starts at the right time.

## Build System: Multi-Stage + Volume Mount

The build system uses **two key optimizations** to prevent crashes on resource-constrained systems:

### 1. Multi-Stage Dockerfile
The Dockerfile is split into two stages:
- **Builder stage:** Compiles all Python dependencies from source (torch, whisper, etc.)
  - Includes build tools (gcc, g++, build-essential)
  - Cached by Docker BuildKit after first build
- **Runtime stage:** Only copies pre-built packages, no compilation
  - Slim image (no build tools)
  - Fast on rebuilds: copies pre-built packages instead of recompiling

**Impact:**
- ✅ Eliminates OOM crashes during `pip install` (heavy compilation)
- ✅ First build takes ~5-10 min (compilation), cached
- ✅ Subsequent rebuilds take ~30 sec (just copy pre-built packages)

### 2. Whisper Model as Volume Mount
The Whisper large-v3 model (~2.9 GB) is **mounted at runtime**, not baked into the image.

**How it works:**
1. Model is downloaded once to `./models/large-v3.pt`
2. Docker containers mount this at runtime: `./models:/app/models:ro`
3. Recorder loads the model from the mounted path at startup

**Impact:**
- ✅ Docker image size: 6+ GB → ~1.5 GB (75% reduction)
- ✅ Build disk requirement: 12 GB → ~4 GB
- ✅ Prevents "No space left on device" errors

**Important:** Always run `python download_model.py` before the first `docker compose up --build`. See BUILD.md for details.

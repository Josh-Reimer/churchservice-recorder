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

# 3. Access the web UI: http://0.0.0.0:5003 (recordings are public)
# Admin login for /config: run `python set_admin_password.py`, paste its
# output into .env, then restart the webserver. No hash = admin login disabled.
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

# Recorder logs go to logs/app.log (file handler only, not stdout).
# ./logs is mounted from the host, so they survive rebuilds:
tail -f logs/app.log
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

`record_stream()` polls `status_url` until the stream goes live, then runs ffmpeg (with `-reconnect`). A segment ends after `MAX_OFFLINE_POLLS` (3) consecutive offline checks, or when ffmpeg exits on its own (it's watched every second). If the stream is back within `MERGE_GAP_MINUTES` (5) of going offline, a new part is recorded and all parts are joined into **one file** with ffmpeg's concat demuxer (`join_parts()`; on failure the parts are kept as separate files). If it stays off longer, the file is finished and the job keeps watching for `REARM_MINUTES` (60; `CHECK_TIMEOUT` after the first file when `sunday_school_break=true`) and records a return as a new file. A whole job is capped at `MAX_RECORDING_MINUTES` (240). Only one job runs per stream URL: a newer trigger sets the older job's stop event and waits for it to finish. Finished recordings are pushed to a `queue.Queue` for transcription; files over Telegram's 50 MB bot limit get a message instead of an upload.

**Backwards compatibility:**

Legacy `sunday_morning_service_time` / `sunday_evening_service_time` fields are auto-migrated to a single Sunday `ServiceSlot` on read — old YAML works without changes.

**RAM-gated transcription:**

Whisper `large-v3`'s resident footprint settles around ~6 GB on CPU (fp16 checkpoint upcast to fp32), but loading it transiently needs more: OOM-killed the recorder container at 6 GB and 8 GB memory limits, and even a 7 GB `MIN_TRANSCRIBE_RAM_GB` pre-load check still got OOM-killed mid-load on a genuine 8 GB / 4 vCPU KVM VM (7.4 GB was "available" at the pre-load check; loading itself pushed past it). Only verified safe at a 10 GB container limit. Since this exceeds the project's <8 GB target, `gpu_transcribe_worker()` checks `psutil.virtual_memory().available` against `MIN_TRANSCRIBE_RAM_GB` (default 10) before loading the model — meaning transcription essentially never runs on an actual 8 GB box, by design. Recording is never gated on this — only transcription is.

- If RAM is insufficient, the worker thread exits without loading the model. Queued files stay in `transcription_queue` untouched. A WARNING is logged, and a one-time Telegram notice is sent (`_low_ram_notified` guards against repeat spam).
- `check_transcription_worker()` runs every `RAM_RECHECK_INTERVAL_MINUTES` and restarts the worker if it isn't alive — so transcription resumes automatically once RAM frees up, without a container restart. `schedule_transcriptions()` is idempotent (guarded by `_worker_start_lock`) so this can't race with the reactive restart in `queue_transcription()`.
- This check reads host-wide available memory via `psutil`, not a cgroup limit. It only reflects reality if the container has **no** `mem_limit`/`deploy.resources.limits` set in `docker-compose.yml` — which is the current, intentional setup. Adding a hard memory cap smaller than the host would let the kernel OOM-kill the container before this check ever sees the pressure.

### `webserver.py` — Flask UI

The recordings page and audio files are public; only the config editor needs a login. CSRF via `flask-wtf`.

Admin login: `ADMIN_USERNAME` + `ADMIN_PASSWORD_HASH` (werkzeug scrypt hash, from `set_admin_password.py`, single-quoted in `.env` because it contains `$`). Without a hash, `/login` returns 503 and the editor is unreachable. `FLASK_SECRET_KEY` signs sessions (random per-process if unset, so restarts log you out). Sessions are re-issued on login, HttpOnly/SameSite=Lax, last `SESSION_LIFETIME_HOURS` (12), and are `Secure` unless `SESSION_COOKIE_SECURE=0` (needed to log in over plain http on the LAN). `MAX_LOGIN_ATTEMPTS` (5) failures per client IP locks it out for `LOGIN_LOCKOUT_MINUTES` (15), in memory. The client IP comes from Cloudflare's `CF-Connecting-IP` only when the request's socket address is in `TRUSTED_PROXY_IPS` (cloudflared on ubuntuserver: `192.168.0.20,100.78.23.97`); direct Tailscale/LAN requests use their real address, so the header can't be spoofed to dodge the lockout. Change the password by re-running `set_admin_password.py`, replacing `ADMIN_PASSWORD_HASH` in `.env`, and `docker compose up -d webserver` (keep `FLASK_SECRET_KEY` unless you also want to sign out every session).

| Route | Purpose |
|-------|---------|
| `/`, `/index` | Public recordings list, grouped by stream, newest first |
| `/recordings/<path>` | Public MP3 download (`<stream_dir>/<file>.mp3`) |
| `/login` | Admin sign-in |
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
| `MIN_TRANSCRIBE_RAM_GB` | 10 GB (env `MIN_TRANSCRIBE_RAM_GB`) | Minimum free RAM required to load Whisper; below this, transcription is skipped, not recording |
| `RAM_RECHECK_INTERVAL_MINUTES` | 20 min (env `RAM_RECHECK_INTERVAL_MINUTES`) | How often to retry starting the transcription worker after a low-RAM skip |
| `MAX_CONCURRENT_RECORDINGS` | 32 (env `MAX_CONCURRENT_RECORDINGS`) | Recording job threads. Jobs mostly sleep while waiting for streams; sizing this to the CPU count (4) made jobs start 15–45 min late |
| `MERGE_GAP_MINUTES` | 5 (env) | Stream back within this long → same file |
| `REARM_MINUTES` | 60 (env) | Keep watching this long for the stream to return after a file ends |
| `MAX_RECORDING_MINUTES` | 240 (env) | Cap on a whole recording job |

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

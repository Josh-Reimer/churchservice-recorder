# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

ByteWorship Recorder automatically records Icecast audio streams (from listentochurch.com) on a Sunday schedule, then transcribes them with OpenAI Whisper. It runs as two Docker containers: one for recording/transcription and one for a Flask web UI.

## Running the project

```bash
# Build and start both containers
docker compose up -d --build

# Shell into the recorder container
docker exec -it church-recorder bash

# Shell into the webserver container
docker exec -it church-webserver bash

# View logs
docker compose logs -f recorder
docker compose logs -f webserver
```

The web UI runs at `http://0.0.0.0:5003`. Default login: `admin` / `42`.

To test recording outside Docker (against a local or live stream):
```bash
python record_now.py           # records until Ctrl+C
python record_now.py 60        # records for 60 seconds
```

## Required `.env` file

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
OPENAI_API_KEY=...
STREAM_URL=...
STREAM_STATUS_URL=...
TIMEZONE=America/St_Johns      # container's local timezone
LOG_LEVEL=INFO
ADMIN_PASS_HASH=...
```

## Architecture

### Two containers, one image

Both `recorder` and `webserver` build from the same `Dockerfile` but run different entry points (`new_recorder.py` vs `webserver.py`). They share the `./recordings` and `./transcriptions` volumes.

### `new_recorder.py` — the main recorder

1. **Config loading** — reads `config/streams.yml` at startup to build a list of `StreamInfo` objects. Each stream has its own timezone, service times, output dirs, and optional `sunday_school_break` flag.
2. **Timezone conversion** — `convert_time()` converts each stream's service time (in the stream's local timezone) into the container's local timezone (`TIMEZONE` env var). All scheduling compares against `now_local()`.
3. **Schedule loop** — `schedule` library calls `check_services()` every `CHECK_INTERVAL` (30 s). On Sundays, `maybe_start_service()` fires recording jobs within a `SERVICE_TRIGGER_WINDOW` (2× CHECK_INTERVAL). A `last_fired` dict on each `StreamInfo` prevents double-firing.
4. **Recording** — `record_stream()` polls `status_url` until the stream is live, then spawns ffmpeg via `run_ffmpeg()`. It monitors the stream continuously; after `MAX_OFFLINE_POLLS` (3) consecutive offline checks it stops ffmpeg. If `sunday_school_break=true`, it waits and records a second segment after the break.
5. **Transcription** — a single background thread (`gpu_transcribe_worker`) loads the Whisper `large-v3` model once and processes a `queue.Queue`. Finished recordings are queued there. The model file at `models/large-v3.pt` is bundled in the image to avoid re-downloading.
6. **Notifications** — Telegram messages/files are sent in fire-and-forget threads when recording starts, finishes, and when transcription finishes.

### `webserver.py` — Flask UI

Simple session-based login (hardcoded `admin`/`42`). Serves recordings from `/app/recordings`, reads paired `.txt` files from `/app/transcriptions` as transcriptions. CSRF protection via `flask-wtf`.

### `config/streams.yml` schema

```yaml
streams:
  - name: short_id
    full_name: Human Readable Name    # used for output subdirectory naming
    url: https://...stream.mp3
    status_url: https://...api/status # must return JSON with {"status": 1} when live
    timezone: America/Chicago
    sunday_morning_service_time: "10:00"
    sunday_evening_service_time: "18:00"
    sunday_school_break: false        # if true, records a second segment after morning service
```

Output directories are derived from `full_name`: lowercased, spaces→`_`, `cong`→`congregation`.

### Key constants (in `new_recorder.py`)

| Name | Default | Purpose |
|------|---------|---------|
| `CHECK_INTERVAL` | 30 s | How often to poll stream status |
| `CHECK_TIMEOUT` | 90 min | Give up waiting for stream after this |
| `MAX_OFFLINE_POLLS` | 3 | Consecutive offline polls before stopping recording |
| `SERVICE_TRIGGER_WINDOW` | 2× CHECK_INTERVAL | How late a trigger can fire |

### Utility scripts

- `record_now.py` — one-shot manual recording, useful for testing streams locally
- `local_transcribe.py` / `sermon_to_text.py` — standalone Whisper transcription of existing files
- `audio_length.py` — prints duration of an audio file
- `test_sched.py` — schedule timing experiments

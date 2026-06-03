# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

ByteWorship Recorder automatically records Icecast audio streams (from listentochurch.com) on a configurable schedule, then transcribes them with OpenAI Whisper. It runs as two Docker containers: one for recording/transcription (`new_recorder.py`) and one for a Flask web UI (`webserver.py`).

## Running the project

```bash
# Build and start both containers
docker compose up -d --build

# Shell into a container
docker exec -it church-recorder bash
docker exec -it church-webserver bash

# Recorder logs go to app.log (file handler only, not stdout)
docker exec church-recorder tail -f app.log
```

Web UI: `http://0.0.0.0:5003` — login `admin` / `42`

To test recording outside Docker:
```bash
python record_now.py        # records until Ctrl+C
python record_now.py 60     # records for 60 seconds
```

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
2. Starts `gpu_transcribe_worker` background thread (loads Whisper `large-v3` once, stays resident)
3. Schedules `check_services()` and `check_config_reload()` every `CHECK_INTERVAL` (30 s)

**Schedule loop:**

`check_services()` iterates every `StreamInfo` → every `ServiceSlot`. For each slot where today's weekday (in the stream's own timezone) matches `slot.day_of_week`, it calls `maybe_start_service()` for morning and evening. `maybe_start_service()` fires `record_stream()` in a thread pool within `SERVICE_TRIGGER_WINDOW` (60 s), using `slot.last_fired` to prevent double-firing.

**Config hot-reload:**

`check_config_reload()` compares `streams.yml` mtime every 30 s. On change it calls `build_services()` and replaces the `services` list, preserving per-slot `last_fired` state so in-progress scheduling isn't disrupted.

**Recording flow:**

`record_stream()` polls `status_url` until the stream goes live, then runs ffmpeg. It monitors continuously and stops after `MAX_OFFLINE_POLLS` (3) consecutive offline checks. If `sunday_school_break=true`, it waits for the stream to return and records a second segment. Finished recordings are pushed to a `queue.Queue` for transcription.

**Backwards compatibility:**

Legacy `sunday_morning_service_time` / `sunday_evening_service_time` fields are auto-migrated to a single Sunday `ServiceSlot` on read — old YAML works without changes.

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

### Utility scripts

- `record_now.py` — one-shot manual recording against a hardcoded local stream URL
- `local_transcribe.py` / `sermon_to_text.py` — standalone Whisper transcription of existing files
- `audio_length.py` — prints duration of an audio file

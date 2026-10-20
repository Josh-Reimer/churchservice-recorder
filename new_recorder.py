import re
import subprocess
import time
import requests
import schedule
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os
import pytz
import logging
from logging.handlers import TimedRotatingFileHandler
import yaml
from concurrent.futures import ThreadPoolExecutor
from threading import Thread, Lock, Event
import atexit
import psutil
import queue
load_dotenv()
TIMEZONE = os.getenv("TIMEZONE", "UTC")
local_tz = pytz.timezone(TIMEZONE)
os.environ["TZ"] = TIMEZONE
time.tzset()

CONFIG_FILE = "config/streams.yml"
_config_mtime = 0.0

print(f"Local timezone: {TIMEZONE}")


def _load_config():
    with open(CONFIG_FILE, "r") as f:
        return yaml.safe_load(f)


def _get_config_mtime():
    try:
        return os.path.getmtime(CONFIG_FILE)
    except OSError:
        return 0.0

def now_local():
    return datetime.now(local_tz)


def convert_time(t, stream_tz, service_date=None):
    if not t or str(t).strip().upper() == "N/A":
        return None
    dt = datetime.strptime(str(t), "%H:%M")
    service_date = service_date or datetime.now(stream_tz).date()
    naive_service_time = datetime.combine(service_date, dt.time())
    try:
        localized = stream_tz.localize(naive_service_time, is_dst=None)
    except pytz.NonExistentTimeError:
        localized = stream_tz.localize(naive_service_time + timedelta(hours=1), is_dst=True)
    except pytz.AmbiguousTimeError:
        localized = stream_tz.localize(naive_service_time, is_dst=False)
    return localized.astimezone(local_tz)

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

services = []

class ServiceSlot:
    def __init__(self, day_of_week, morning_time_text, evening_time_text, morning_time, evening_time, sunday_school_break=False):
        self.day_of_week = day_of_week      # int 0=Mon … 6=Sun
        self.morning_time_text = morning_time_text
        self.evening_time_text = evening_time_text
        self.morning_time = morning_time    # tz-aware datetime in local_tz, or None
        self.evening_time = evening_time
        self.sunday_school_break = sunday_school_break
        self.last_fired = {}                # "morning"/"evening" -> date last fired


class StreamInfo:
    def __init__(self, name, url, status_url, timezone, stream_tz, audio_dir, transcription_dir, slots, enabled=True):
        self.name = name
        self.url = url
        self.status_url = status_url
        self.timezone = timezone
        self.stream_tz = stream_tz
        self.audio_dir = audio_dir
        self.transcription_dir = transcription_dir
        self.slots = slots      # list[ServiceSlot]
        self.enabled = enabled
       

OUTPUT_DIR = "./recordings"
TRANSCRIPTIONS_DIR = "./transcriptions"
transcription_queue = queue.Queue(maxsize=0)
_transcription_worker = None
_worker_start_lock = Lock()
_low_ram_notified = False

# Logs live in LOG_DIR, which docker-compose mounts from the host (./logs) so
# they survive rebuilds — app.log used to sit inside the container and every
# `docker compose up --build` wiped the history.
LOG_DIR = os.getenv("LOG_DIR", "logs")
_log_file_error = None
try:
    os.makedirs(LOG_DIR, exist_ok=True)
    # Create timed rotating handler
    handler = TimedRotatingFileHandler(
        os.path.join(LOG_DIR, 'app.log'),
        when='D',     # Rotate at midnight
        interval=14,          # Every 14 days
        backupCount=26,       # Keep about 365 days worth at a 14-day interval
        atTime=None,         # At midnight (default)
        utc=False           # Use local time
    )
except OSError as e:
    # e.g. the host ./logs dir was created by Docker as root. Don't take the
    # recorder down over it — log to stderr (`docker logs`) instead.
    _log_file_error = e
    handler = logging.StreamHandler()

# Set format with date
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(handler)

if _log_file_error:
    logger.error(f"Cannot write {LOG_DIR}/app.log ({_log_file_error}) — logging to stderr instead.")
logger.info("This will rotate every 14 days at midnight")
CHECK_INTERVAL = 30  # seconds
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
if not TELEGRAM_ENABLED:
    print("WARNING: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — Telegram notifications disabled.")
    logger.warning("Telegram notifications disabled: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing.")
CHECK_TIMEOUT = 90 # minutes
MAX_OFFLINE_POLLS = 3  # consecutive offline/error polls before treating stream as ended
SERVICE_TRIGGER_WINDOW = timedelta(seconds=max(60, CHECK_INTERVAL * 2))

# Many streams blip on for a few minutes before the service (or drop briefly
# mid-service) and then come back. A stream that returns within
# MERGE_GAP_MINUTES of going offline keeps recording into the same file; one
# that returns later (but within REARM_MINUTES) gets a new file in the same
# job. MAX_RECORDING_MINUTES caps a whole job so a stream left on all day
# can't record for hours.
MERGE_GAP_MINUTES = int(os.getenv("MERGE_GAP_MINUTES", "5"))
REARM_MINUTES = int(os.getenv("REARM_MINUTES", "60"))
MAX_RECORDING_MINUTES = int(os.getenv("MAX_RECORDING_MINUTES", "240"))
TELEGRAM_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # Bot API sendDocument limit

# Whisper large-v3's resident footprint settles around ~6 GB on CPU (fp16
# checkpoint upcast to fp32), but loading it transiently needs more than
# that: a 7 GB threshold still got OOM-killed mid-load on a genuine 8 GB VM
# (7.4 GB was "available" at the pre-load check, but the load itself pushed
# past it). Verified safe at a 10 GB container memory limit on the same
# image. Below this, skip transcription instead of risking an OOM kill —
# recording always takes priority. Retried periodically in case RAM frees up
# later (see RAM_RECHECK_INTERVAL_MINUTES).
MIN_TRANSCRIBE_RAM_GB = float(os.getenv("MIN_TRANSCRIBE_RAM_GB", "10"))
RAM_RECHECK_INTERVAL_MINUTES = int(os.getenv("RAM_RECHECK_INTERVAL_MINUTES", "20"))

# ffmpeg here just copies the stream (-c copy, no re-encode) so each job is
# cheap, and a job spends most of its life asleep waiting for the stream to
# come online. Sizing the pool to the CPU count (4 on the production VM)
# starved it: streams in several timezones trigger at the same container
# time, jobs waiting on offline streams held every slot for up to 90 min,
# and queued jobs started 15-45 min late. One slot per stream is the cap.
MAX_CONCURRENT_RECORDINGS = int(os.getenv("MAX_CONCURRENT_RECORDINGS", "32"))
_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_RECORDINGS)

# One recording job per stream URL at a time. A newer trigger asks the older
# job to finish (via its stop event) and waits for it before starting.
_stream_jobs_lock = Lock()
_stream_locks = {}        # url -> Lock held for the life of a recording job
_stream_stop_events = {}  # url -> Event of the job currently running

def stream_available(status_url):
    """Check stream status from external API and print status."""
    url = status_url
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        if data.get("full") == 1:
            print("This system is full.")
            logger.warning("This system is full.")
        elif not data.get("autoscale") and data.get("percentage", 0) > 74:
            print(f"This system is {round(data.get('percentage', 0))}% full.")
            print(f"Status message: {data.get('message')}")
            logger.warning(f"This system is {round(data.get('percentage', 0))}% full. Status message: {data.get('message')}")
        if data.get("status") == 1:
            return True
        else:
            return False
    except requests.RequestException as e:
        print(f"Error checking stream status: {e}")
        logger.error(f"Status check failed: {e}")
        return None

def notify_telegram(text: str):
    """Send a Telegram message if notifications are enabled."""
    if TELEGRAM_ENABLED:
        Thread(target=send_telegram_message, args=(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, text)).start()


def notify_telegram_file(file_path: str, caption: str = ""):
    """Send a Telegram file if notifications are enabled."""
    if not TELEGRAM_ENABLED:
        return
    try:
        size = os.path.getsize(file_path)
    except OSError:
        size = 0
    if size > TELEGRAM_MAX_UPLOAD_BYTES:
        logger.info(f"Not uploading {file_path} to Telegram: {size / 1e6:.0f} MB is over the 50 MB bot limit.")
        notify_telegram(f"{caption} ({size / 1e6:.0f} MB — too large to upload to Telegram)")
        return
    send_telegram_file(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, file_path, caption=caption)


def _redact(text) -> str:
    """Strip the bot token out of error text (requests errors include the URL)."""
    text = str(text)
    return text.replace(TELEGRAM_BOT_TOKEN, "<redacted>") if TELEGRAM_BOT_TOKEN else text


def _available_ram_gb():
    return psutil.virtual_memory().available / (1024 ** 3)


def _has_enough_ram_for_transcription():
    """Check whether there's enough free RAM to safely load Whisper.

    Recording never depends on this — only transcription does. Notifies once
    (not on every retry) the first time transcription is skipped.
    """
    global _low_ram_notified
    available = _available_ram_gb()
    if available < MIN_TRANSCRIBE_RAM_GB:
        logger.warning(
            f"Skipping transcription: {available:.1f} GB RAM available, "
            f"need at least {MIN_TRANSCRIBE_RAM_GB:.1f} GB to load Whisper safely. "
            f"Recording is unaffected. Will retry every {RAM_RECHECK_INTERVAL_MINUTES} min."
        )
        if not _low_ram_notified:
            notify_telegram(
                f"Transcription paused: only {available:.1f} GB RAM available "
                f"({MIN_TRANSCRIBE_RAM_GB:.1f} GB needed for Whisper). Recordings are "
                f"unaffected and will continue normally. Transcription will resume "
                f"automatically once enough RAM is free."
            )
            _low_ram_notified = True
        return False
    return True


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> dict:
    """
    Send a message to a Telegram chat via the Bot API.
    
    Args:
        bot_token (str): The Telegram bot token from BotFather.
        chat_id (str): The chat ID or username (e.g., "@channelname").
        text (str): The message text to send.
    
    Returns:
        dict: Telegram API JSON response.
    """
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()  # Raises for HTTP errors
        logger.info(f"Sent Telegram message to {chat_id}")
        return response.json()
    except requests.RequestException as e:
        try:
            print("Telegram response:", response.text)
            logger.error(f"Telegram API said: {response.text}")
        except Exception:
            logger.error(f"Telegram send failed (no response): {_redact(e)}")
            print(f"No response text from Telegram. Error: {_redact(e)}")
        # Not re-raised: this runs in a bare Thread, where an uncaught
        # exception prints a traceback (with the token in the URL) to stderr.
        return {"ok": False, "error": _redact(e)}



def send_telegram_file(bot_token: str, chat_id: str, file_path: str, caption: str = "") -> dict:
    """
    Upload a file to a Telegram chat via the Bot API.
    
    Args:
        bot_token (str): The Telegram bot token from BotFather.
        chat_id (str): The chat ID or username (e.g., "@channelname").
        file_path (str): Path to the file to send.
        caption (str): Optional caption for the file.
    
    Returns:
        dict: Telegram API JSON response.
    """
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    
    try:
        with open(file_path, "rb") as f:
            files = {"document": f}
            data = {"chat_id": chat_id, "caption": caption}
            response = requests.post(url, data=data, files=files, timeout=30)
            response.raise_for_status()
            logger.info(f"Sent Telegram file {file_path} to {chat_id}")
            return response.json()
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
        return {"ok": False, "error": f"File not found: {file_path}"}
    except requests.RequestException as e:
        logger.error(f"Error sending Telegram file: {_redact(e)}")
        return {"ok": False, "error": _redact(e)}

#send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, "Church Service Recorder started.")


def stop_process(process):
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def recording_timestamp():
    return now_local().strftime("%Y-%m-%d_%H-%M")


def _sleep(seconds, stop_event=None):
    """Sleep, waking early if stop_event is set."""
    if stop_event is None:
        time.sleep(seconds)
    else:
        stop_event.wait(seconds)


def wait_for_stream_online(status_url, context_label, timeout_minutes=CHECK_TIMEOUT,
                           notify_on_timeout=True, deadline=None, stop_event=None):
    """Wait for stream to come online.

    Returns True once online. Returns False after `timeout_minutes` of offline
    polls (None = no limit), once the time.monotonic() `deadline` passes, or
    when `stop_event` is set.
    """
    offline_seconds = 0
    while True:
        if stop_event is not None and stop_event.is_set():
            return False
        available = stream_available(status_url)
        if available is True:
            return True
        elif available is False:
            offline_seconds += CHECK_INTERVAL
            print(f"[{now_local()}] Stream offline. Checking again in {CHECK_INTERVAL} seconds... ({context_label})")
            logger.info(f"Stream offline ({context_label}). Checking again in {CHECK_INTERVAL} seconds...")
            if timeout_minutes is not None and offline_seconds >= timeout_minutes * 60:
                print(f"[{now_local()}] Stream did not become available within {timeout_minutes} minutes. Exiting ({context_label}).")
                logger.warning(f"Stream did not become available within {timeout_minutes} minutes ({context_label}). Exiting.")
                if notify_on_timeout:
                    notify_telegram(f"Stream did not become available within {timeout_minutes} minutes ({context_label}). Exiting.")
                return False
        else:
            logger.warning(f"Status check failed for {status_url} ({context_label}), retrying in {CHECK_INTERVAL} seconds...")
        sleep_for = CHECK_INTERVAL
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            sleep_for = min(sleep_for, remaining)
        _sleep(sleep_for, stop_event)


def watch_recording(process, status_url, deadline, stop_event):
    """Watch a running ffmpeg until it should stop.

    Returns (reason, offline_since). reason is "offline" (MAX_OFFLINE_POLLS
    consecutive offline/error polls), "ffmpeg_exited" (ffmpeg died on its own,
    e.g. the server dropped the connection while the status API still said
    online), "capped" (job deadline reached) or "stopped" (a newer job for
    this stream took over). offline_since is the time.monotonic() of the first
    offline poll in the current streak, or None.
    """
    consecutive_offline = 0
    offline_since = None
    while True:
        available = stream_available(status_url)
        if available is True:
            consecutive_offline = 0
            offline_since = None
        else:
            consecutive_offline += 1
            if offline_since is None:
                offline_since = time.monotonic()
            if consecutive_offline >= MAX_OFFLINE_POLLS:
                return "offline", offline_since
        # Wait for the next poll, but notice ffmpeg dying within a second.
        next_poll = time.monotonic() + CHECK_INTERVAL
        while time.monotonic() < next_poll:
            if stop_event.is_set():
                return "stopped", offline_since
            if time.monotonic() >= deadline:
                return "capped", offline_since
            if process.poll() is not None:
                return "ffmpeg_exited", offline_since
            stop_event.wait(min(1, max(0, next_poll - time.monotonic())))


def join_parts(output_file, parts):
    """Join recording parts (in order) into output_file.

    Returns the list of files to deliver: [output_file] normally, or the
    separate parts if joining failed, so no audio is ever thrown away.
    """
    parts = [p for p in parts if os.path.exists(p) and os.path.getsize(p) > 0]
    if not parts:
        return []
    if len(parts) == 1:
        if parts[0] != output_file:
            os.replace(parts[0], output_file)
        return [output_file]

    list_path = f"{output_file}.parts.txt"
    joined = f"{os.path.splitext(output_file)[0]}.joined.mp3"
    with open(list_path, "w") as f:
        for p in parts:
            f.write("file '" + os.path.abspath(p).replace("'", "'\\''") + "'\n")
    cmd = [
        "ffmpeg", "-y",
        "-nostats", "-loglevel", "warning",
        "-f", "concat", "-safe", "0",
        "-i", list_path,
        "-c", "copy",
        joined,
    ]
    try:
        result = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
        ok = result.returncode == 0 and os.path.exists(joined) and os.path.getsize(joined) > 0
        error = result.stderr.strip()[-500:]
    except Exception as e:
        ok, error = False, str(e)
    finally:
        try:
            os.remove(list_path)
        except OSError:
            pass

    if not ok:
        logger.error(f"Could not join {len(parts)} parts of {output_file} — keeping them as separate files: {error}")
        if os.path.exists(joined):
            os.remove(joined)
        return parts

    os.replace(joined, output_file)
    for p in parts:
        if p != output_file:
            os.remove(p)
    logger.info(f"Joined {len(parts)} parts into {output_file}.")
    return [output_file]


MAX_SHORT_PARTS = 10  # consecutive near-instant ffmpeg failures before giving up on a file


def record_one_file(service_type, url, status_url, output_dir, transcription_dir, deadline, stop_event):
    """Record one file, rejoining the stream if it returns within MERGE_GAP_MINUTES.

    ffmpeg is restarted into a new part whenever it dies or the stream comes
    back, and the parts are joined into one file at the end. Returns why the
    file ended: "offline", "capped", "stopped" or "failed".
    """
    timestamp = recording_timestamp()
    base = f"{output_dir}/recording_{timestamp}"
    output_file = f"{base}.mp3"
    parts = []
    gap_start = None  # time.monotonic() the audio was lost, while trying to rejoin
    short_parts = 0
    outcome = None
    while outcome is None:
        part_base = base if not parts else f"{base}.part{len(parts) + 1}"
        if not parts:
            print(f"[{now_local()}] Stream online! Starting recording to {output_file}")
            logger.info(f"Stream online! Starting recording to {output_file}")
        else:
            logger.info(f"Stream back — continuing {output_file} (part {len(parts) + 1}).")
        try:
            process = run_ffmpeg(service_type, url, part_base, "mp3")
        except Exception as e:
            logger.error(f"FAILED TO START RECORDING for {service_type} ({url}): {e}", exc_info=e)
            print(f"[{now_local()}] ERROR: ffmpeg failed to start for {service_type} — recording aborted. See app.log for details.")
            outcome = "failed"
            break
        parts.append(f"{part_base}.mp3")
        started = time.monotonic()
        if len(parts) == 1:
            logger.info(f"Started ffmpeg process with PID {process.pid} for recording.")
            notify_telegram(f"Recording started at {timestamp}.")

        reason, offline_since = watch_recording(process, status_url, deadline, stop_event)
        stop_process(process)
        ran_for = time.monotonic() - started
        if reason in ("capped", "stopped"):
            outcome = reason
            break
        if reason == "ffmpeg_exited":
            logger.warning(f"ffmpeg exited on its own after {ran_for / 60:.1f} min recording {output_file} (see its .ffmpeg.log).")

        # A part that only lasted seconds didn't really get the stream back, so
        # it doesn't restart the gap clock.
        lost_at = offline_since or time.monotonic()
        if ran_for >= 60:
            short_parts = 0
            gap_start = lost_at
        else:
            short_parts += 1
            if gap_start is None:
                gap_start = lost_at
            if short_parts >= MAX_SHORT_PARTS:
                logger.error(f"ffmpeg keeps exiting immediately for {output_file} — giving up on this recording.")
                outcome = "failed"
                break
        if ran_for < CHECK_INTERVAL:
            _sleep(CHECK_INTERVAL - ran_for, stop_event)  # don't hammer the server

        rejoin_by = min(deadline, gap_start + MERGE_GAP_MINUTES * 60)
        if wait_for_stream_online(status_url, f"{service_type}_gap", timeout_minutes=None,
                                  notify_on_timeout=False, deadline=rejoin_by, stop_event=stop_event):
            continue
        if stop_event.is_set():
            outcome = "stopped"
        elif time.monotonic() >= deadline:
            outcome = "capped"
        else:
            outcome = "offline"

    if parts:
        print(f"[{now_local()}] Stream stopped. Ending recording.")
        logger.info(f"Stream stopped. Ending recording of {output_file}.")
        for f in join_parts(output_file, parts):
            notify_telegram_file(f, caption=f"Recording finished: {f}")
            queue_transcription(f, transcription_dir)
    return outcome


def record_stream(service_type, url, status_url, output_dir, transcription_dir, has_sunday_school=False):
    """Record the stream until it becomes unavailable.

    Only one job runs per stream: a newer trigger (e.g. the evening service
    while the morning job is still going) asks the older job to finish its
    current file, then waits for it.
    """
    stop_event = Event()
    with _stream_jobs_lock:
        previous = _stream_stop_events.get(url)
        _stream_stop_events[url] = stop_event
        stream_lock = _stream_locks.setdefault(url, Lock())
    if previous is not None:
        logger.warning(f"An earlier recording job for {output_dir} is still running — asking it to finish so {service_type} can start.")
        previous.set()
    with stream_lock:
        try:
            _record_stream_job(service_type, url, status_url, output_dir, transcription_dir, has_sunday_school, stop_event)
        finally:
            with _stream_jobs_lock:
                if _stream_stop_events.get(url) is stop_event:
                    del _stream_stop_events[url]


def _record_stream_job(service_type, url, status_url, output_dir, transcription_dir, has_sunday_school, stop_event):
    print(f"[{now_local()}] Waiting for stream availability...")
    logger.info(f"Waiting for stream availability for {service_type}")
    if not wait_for_stream_online(status_url, f"initial_{service_type}", stop_event=stop_event):
        return

    deadline = time.monotonic() + MAX_RECORDING_MINUTES * 60
    files_recorded = 0
    while True:
        outcome = record_one_file(service_type, url, status_url, output_dir, transcription_dir, deadline, stop_event)
        files_recorded += 1
        if outcome == "capped":
            logger.warning(f"{service_type} recording for {output_dir} hit the {MAX_RECORDING_MINUTES}-minute cap with the stream still online — stopping.")
            notify_telegram(f"Recording stopped after {MAX_RECORDING_MINUTES} minutes ({output_dir}) — the stream was still online.")
            return
        if outcome != "offline":
            return

        # The stream stayed off longer than MERGE_GAP_MINUTES. Streams often
        # come back after a pre-service blip or a Sunday school break, so keep
        # watching and record the return as a new file.
        if has_sunday_school and files_recorded == 1:
            notify_telegram("Sunday morning recording finished. Starting next recording after sunday school")
            label, wait_minutes, notify = "sunday_school_break", CHECK_TIMEOUT, True
        else:
            label, wait_minutes, notify = f"{service_type}_rearm", REARM_MINUTES, False
        if not wait_for_stream_online(status_url, label, timeout_minutes=wait_minutes,
                                      notify_on_timeout=notify, deadline=deadline, stop_event=stop_event):
            return


def run_ffmpeg(name, url, output_path, output_format="mp3"):
    """Run an FFmpeg process and return the process handle."""
    cmd = [
        "ffmpeg", "-y",
        "-nostats", "-loglevel", "warning",
        # The server sometimes drops the connection mid-service ("Stream ends
        # prematurely"); reconnect in-process instead of exiting.
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10",
        "-i", url,
        "-c", "copy",
        f"{output_path}.{output_format}"
    ]
    logger.info(f"Starting FFmpeg for {name}: {url} → {output_path}.{output_format}")

    try:
        # stderr must not be an unread PIPE: once its 64 KB buffer fills,
        # ffmpeg blocks on the write and the recording silently stalls.
        # Send it to a log file next to the recording instead.
        with open(f"{output_path}.ffmpeg.log", "w") as stderr_log:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr_log
            )
        logger.info(f"FFmpeg process started (PID {process.pid})")
        return process  # <— Return immediately
    except Exception as e:
        logger.error(f"Error running FFmpeg for {name}: {e}")
        raise

def gpu_transcribe_worker():
    if not _has_enough_ram_for_transcription():
        return  # thread exits; queued files stay queued until a later retry

    import whisper
    import gc
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info(f"Loading Whisper model on {device}")

    # Load the model once and keep it resident for all queued files.
    # This avoids reloading (minutes of overhead) between recordings.
    try:
        # Use the local model file mounted as a volume (./models:/app/models).
        # If not available, falls back to downloading from HuggingFace.
        local_model_path = "/app/models/large-v3.pt"
        model_name_or_path = local_model_path if os.path.exists(local_model_path) else "large-v3"
        model = whisper.load_model(model_name_or_path, device=device)
    except Exception as e:
        logger.error(f"Failed to load Whisper model: {e}")
        return

    global _low_ram_notified
    if _low_ram_notified:
        logger.info("Whisper model loaded — transcription has resumed.")
        notify_telegram("Transcription has resumed — enough RAM is free again.")
        _low_ram_notified = False

    try:
        while True:
            # Fetch the [audio_file_path, transcription_dir] pair as a single item.
            # Bug fix: previously called queue.get() twice, causing a deadlock / wrong dir.
            try:
                item = transcription_queue.get(timeout=5)
            except queue.Empty:
                continue
            if item is None:
                logger.info("Transcription worker received stop signal. Exiting.")
                break

            audio_file_path, transcription_dir = item[0], item[1]
            logger.info(f"Transcribing {audio_file_path} → {transcription_dir}")

            try:
                # use fp16 on CUDA to reduce memory & speed up
                result = model.transcribe(audio_file_path, fp16=(device == "cuda"))
                text = result.get("text", "")
                print(text)

                transcription_text_file = os.path.join(
                    transcription_dir,
                    os.path.basename(audio_file_path).replace(".mp3", ".txt")
                )
                with open(transcription_text_file, "w", encoding="utf-8") as f:
                    f.write(text)

                logger.info(f"Transcription saved to {transcription_text_file}")
                notify_telegram_file(transcription_text_file, caption=f"Transcription finished: {transcription_text_file}")

            except Exception as e:
                logger.error(f"Transcription error for {audio_file_path}: {e}")

            finally:
                transcription_queue.task_done()
    finally:
        # Clean up GPU memory when the worker exits for any reason.
        try:
            del model
            if device == "cuda":
                torch.cuda.empty_cache()
        except Exception:
            pass
        gc.collect()



def kill_ffmpeg_children():
    try:
        processes = psutil.process_iter(['pid', 'name'])
        for proc in processes:
            if 'ffmpeg' in (proc.info.get('name') or ''):
                try:
                    proc.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
    except (psutil.Error, PermissionError):
        logger.warning("Unable to scan for ffmpeg child processes during shutdown.")

atexit.register(kill_ffmpeg_children)

def shutdown_transcription_worker():
    """Gracefully shut down the transcription worker thread."""
    global _transcription_worker
    if _transcription_worker and _transcription_worker.is_alive():
        try:
            logger.info("Sending stop signal to transcription worker...")
            transcription_queue.put(None)
            _transcription_worker.join(timeout=10)
            if _transcription_worker.is_alive():
                logger.warning("Transcription worker did not exit cleanly within 10 seconds.")
            else:
                logger.info("Transcription worker shut down cleanly.")
        except Exception as e:
            logger.error(f"Error during transcription worker shutdown: {e}")

atexit.register(shutdown_transcription_worker)

def threaded(job_func, *args):
    if _executor._work_queue.qsize() > 0:
        logger.warning(
            f"All {MAX_CONCURRENT_RECORDINGS} recording slots are busy — "
            f"{job_func.__name__} will start as soon as one frees up."
        )
    future = _executor.submit(job_func, *args)
    def _on_done(f):
        if f.cancelled():
            logger.warning(f"Recording job {job_func.__name__} was cancelled before it could run.")
            return
        exc = f.exception()
        if exc:
            logger.error(f"Uncaught exception in {job_func.__name__}: {exc}", exc_info=exc)
    future.add_done_callback(_on_done)

def schedule_transcriptions():
    """Start the background transcription worker thread, if it isn't already running."""
    global _transcription_worker
    with _worker_start_lock:
        if _transcription_worker and _transcription_worker.is_alive():
            return _transcription_worker
        _transcription_worker = Thread(target=gpu_transcribe_worker, daemon=True)
        _transcription_worker.start()
        logger.info("Transcription worker thread started.")
        return _transcription_worker


def check_transcription_worker():
    """Periodically retry starting the transcription worker if it was previously
    skipped or exited (e.g. due to insufficient RAM at the time)."""
    if not _transcription_worker or not _transcription_worker.is_alive():
        logger.info("Transcription worker not running — checking whether it can start now.")
        schedule_transcriptions()


def queue_transcription(audio_file_path, transcription_dir):
    """Safely queue an audio file for transcription, restarting worker if needed."""
    global _transcription_worker

    if not _transcription_worker or not _transcription_worker.is_alive():
        logger.warning("Transcription worker is not running. Attempting to restart...")
        try:
            schedule_transcriptions()
        except Exception as e:
            logger.error(f"Failed to restart transcription worker: {e}")
            logger.error(f"Transcription WILL NOT occur for {audio_file_path}")
            return False

    try:
        transcription_queue.put([audio_file_path, transcription_dir])
        logger.info(f"Queued {audio_file_path} for transcription.")
        return True
    except Exception as e:
        logger.error(f"Failed to queue {audio_file_path} for transcription: {e}")
        return False


def service_time_for_slot(slot, stream_tz, service_date, time_of_day):
    text = slot.morning_time_text if time_of_day == "morning" else slot.evening_time_text
    return convert_time(text, stream_tz, service_date)


def maybe_start_service(stream_info, slot, time_of_day, scheduled_time, current_time):
    if scheduled_time is None:
        return
    service_date = scheduled_time.date()
    if slot.last_fired.get(time_of_day) == service_date:
        return
    label = f"{DAYS[slot.day_of_week]}_{time_of_day}"
    if scheduled_time <= current_time < scheduled_time + SERVICE_TRIGGER_WINDOW:
        slot.last_fired[time_of_day] = service_date
        threaded(
            record_stream,
            label,
            stream_info.url,
            stream_info.status_url,
            stream_info.audio_dir,
            stream_info.transcription_dir,
            slot.sunday_school_break if time_of_day == "morning" else False,
        )
        logger.info(f"Triggered {stream_info.name} {label} at {current_time}; scheduled for {scheduled_time}")
    elif current_time > scheduled_time + SERVICE_TRIGGER_WINDOW:
        logger.warning(f"Missed trigger window for {stream_info.name} {label} scheduled at {scheduled_time} — container may have started late.")
        slot.last_fired[time_of_day] = service_date


def check_services():
    current_time = now_local()
    for stream_info in services:
        if not stream_info.enabled:
            continue
        try:
            for slot in stream_info.slots:
                now_in_stream_tz = current_time.astimezone(stream_info.stream_tz)
                if now_in_stream_tz.weekday() != slot.day_of_week:
                    continue
                service_date = now_in_stream_tz.date()
                maybe_start_service(stream_info, slot, "morning",
                    service_time_for_slot(slot, stream_info.stream_tz, service_date, "morning"),
                    current_time)
                maybe_start_service(stream_info, slot, "evening",
                    service_time_for_slot(slot, stream_info.stream_tz, service_date, "evening"),
                    current_time)
        except Exception as e:
            # One broken stream must not kill the schedule loop for the rest.
            logger.error(f"Scheduling check failed for stream '{stream_info.name}': {e}", exc_info=e)


def build_services(config):
    """Build the services list from a loaded config dict.

    A stream with invalid config (unknown timezone, malformed time, …) is
    skipped with an error instead of taking down the whole recorder.
    """
    new_services = []
    seen_urls = set()
    for stream in config.get("streams", []):
        try:
            new_services.append(_build_stream_info(stream, seen_urls))
        except SkipStream:
            continue
        except Exception as e:
            name = stream.get("name", "Unknown") if isinstance(stream, dict) else "Unknown"
            logger.error(f"Skipping stream '{name}' — invalid config: {e}")
            notify_telegram(f"⚠️ Stream '{name}' has invalid config and will NOT be recorded: {e}")
    return new_services


class SkipStream(Exception):
    """Raised to skip a stream without treating it as a config error."""


def _build_stream_info(stream, seen_urls):
    """Build one StreamInfo from a raw config entry. Raises on invalid config."""
    url = stream.get("url", "")
    if url in seen_urls:
        logger.warning(f"Duplicate stream URL '{url}' in streams.yml — skipping.")
        raise SkipStream(url)
    seen_urls.add(url)
    name = stream.get("name", "Unknown")
    tz_name = stream.get("timezone", "UTC")
    stream_tz = pytz.timezone(tz_name)
    full_name = stream.get("full_name", name)
    safe_name = (
        full_name.lower()
        .replace(" ", "_").replace(",", "").replace(".", "")
    )
    # Expand the "cong" abbreviation to "congregation", but skip it where
    # "congregation" is already spelled out in full (avoids "congregationregation").
    safe_name = re.sub(r"cong(?!regation)", "congregation", safe_name)
    safe_name = safe_name.replace("-", "_")
    output_dir = os.path.join(OUTPUT_DIR, safe_name)
    transcription_dir = os.path.join(TRANSCRIPTIONS_DIR, safe_name)
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(transcription_dir, exist_ok=True)
    enabled = stream.get("enabled", True)

    # Support new `services` list; fall back to legacy sunday_*_service_time fields
    raw_slots = stream.get("services")
    if raw_slots is None:
        morning = stream.get("sunday_morning_service_time")
        evening = stream.get("sunday_evening_service_time")
        ssb = stream.get("sunday_school_break", False)
        raw_slots = [{"day": "sunday", "morning": morning, "evening": evening,
                      "sunday_school_break": ssb}] if (morning or evening) else []

    slots = []
    for rs in raw_slots:
        day_str = str(rs.get("day", "sunday")).lower()
        dow = DAYS.index(day_str) if day_str in DAYS else 6
        m_text = rs.get("morning")
        e_text = rs.get("evening")
        slots.append(ServiceSlot(
            day_of_week=dow,
            morning_time_text=m_text,
            evening_time_text=e_text,
            morning_time=convert_time(m_text, stream_tz),
            evening_time=convert_time(e_text, stream_tz),
            sunday_school_break=rs.get("sunday_school_break", False),
        ))

    stream_info = StreamInfo(
        name=name,
        url=url,
        status_url=stream.get("status_url", ""),
        timezone=tz_name,
        stream_tz=stream_tz,
        audio_dir=output_dir,
        transcription_dir=transcription_dir,
        slots=slots,
        enabled=enabled,
    )
    for slot in slots:
        logger.info(f"Loaded {name}: {DAYS[slot.day_of_week]} morning={slot.morning_time}, evening={slot.evening_time}")
    return stream_info


def check_config_reload():
    """Reload streams.yml if it has changed on disk."""
    global services, _config_mtime
    current_mtime = _get_config_mtime()
    if current_mtime <= _config_mtime:
        return
    _config_mtime = current_mtime
    logger.info("streams.yml changed — reloading config.")
    try:
        config = _load_config()
        new_services = build_services(config)
    except Exception as e:
        logger.error(f"Config reload failed — keeping previous config: {e}")
        notify_telegram(f"⚠️ streams.yml reload failed — keeping previous config: {e}")
        return
    old_by_name = {s.name: s for s in services}
    for s in new_services:
        if s.name in old_by_name:
            old_slots = old_by_name[s.name].slots
            for i, slot in enumerate(s.slots):
                if i < len(old_slots):
                    slot.last_fired = old_slots[i].last_fired
    services = new_services
    logger.info(f"Config reloaded — {len(services)} streams active.")


if __name__ == "__main__":
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
    except Exception as e:
        logger.error(f"Error creating output directory {OUTPUT_DIR}: {e}")
    try:
        os.makedirs(TRANSCRIPTIONS_DIR, exist_ok=True)
    except Exception as e:
        logger.error(f"Error creating transcriptions directory {TRANSCRIPTIONS_DIR}: {e}")
    
    config = _load_config()
    services = build_services(config)
    _config_mtime = _get_config_mtime()

    print(f"Loaded {len(services)} streams:")
    for s in services:
        for slot in s.slots:
            print(f"  {s.name}: {DAYS[slot.day_of_week]} morning={slot.morning_time}, evening={slot.evening_time}")

    schedule_transcriptions()
    schedule.every(CHECK_INTERVAL).seconds.do(check_services)
    schedule.every(CHECK_INTERVAL).seconds.do(check_config_reload)
    schedule.every(RAM_RECHECK_INTERVAL_MINUTES).minutes.do(check_transcription_worker)

    logger.info("Entering schedule loop. Waiting for Sunday services...")
    print("Recorder running. Waiting for scheduled services (Ctrl+C to stop).")
    while True:
        schedule.run_pending()
        time.sleep(1)

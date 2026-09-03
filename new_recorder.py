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
from threading import Thread, Lock
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

# Create timed rotating handler
handler = TimedRotatingFileHandler(
    'app.log',
    when='D',     # Rotate at midnight
    interval=14,          # Every 14 days
    backupCount=26,       # Keep about 365 days worth at a 14-day interval
    atTime=None,         # At midnight (default)
    utc=False           # Use local time
)

# Set format with date
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(handler)

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
# cheap, but 16 concurrent jobs was a fixed guess unrelated to the actual
# host. Scale it to the CPU instead so weak hardware (e.g. an i3) isn't asked
# to juggle more simultaneous recordings than it realistically can.
MAX_CONCURRENT_RECORDINGS = int(os.getenv("MAX_CONCURRENT_RECORDINGS", str(max(2, os.cpu_count() or 4))))
_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_RECORDINGS)

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
    if TELEGRAM_ENABLED:
        send_telegram_file(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, file_path, caption=caption)


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
            logger.error(f"Telegram send failed (no response): {e}")
            print(f"No response text from Telegram. Error: {e}")
        raise



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
        logger.error(f"Error sending Telegram file: {e}")
        return {"ok": False, "error": str(e)}

#send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, "Church Service Recorder started.")


def stop_process(process):
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def recording_timestamp():
    return now_local().strftime("%Y-%m-%d_%H-%M")


def wait_for_stream_online(status_url, context_label):
    """Wait for stream to come online, with timeout. Returns True if online, False if timeout."""
    offline_seconds = 0
    while True:
        available = stream_available(status_url)
        if available is True:
            return True
        elif available is False:
            offline_seconds += CHECK_INTERVAL
            print(f"[{now_local()}] Stream offline. Checking again in {CHECK_INTERVAL} seconds... ({context_label})")
            logger.info(f"Stream offline ({context_label}). Checking again in {CHECK_INTERVAL} seconds...")
            if offline_seconds >= CHECK_TIMEOUT * 60:
                print(f"[{now_local()}] Stream did not become available within {CHECK_TIMEOUT} minutes. Exiting ({context_label}).")
                logger.warning(f"Stream did not become available within {CHECK_TIMEOUT} minutes ({context_label}). Exiting.")
                notify_telegram(f"Stream did not become available within {CHECK_TIMEOUT} minutes ({context_label}). Exiting.")
                return False
        else:
            logger.warning(f"Status check failed for {status_url} ({context_label}), retrying in {CHECK_INTERVAL} seconds...")
        time.sleep(CHECK_INTERVAL)


def monitor_stream_while_recording(status_url, context_label):
    """Monitor stream status while recording. Returns when stream becomes unavailable."""
    consecutive_offline = 0
    while True:
        available = stream_available(status_url)
        if available is True:
            consecutive_offline = 0
        elif available is False or available is None:
            consecutive_offline += 1
            if consecutive_offline >= MAX_OFFLINE_POLLS:
                break
        time.sleep(CHECK_INTERVAL)


def record_stream(service_type, url, status_url, output_dir, transcription_dir, has_sunday_school=False):
    """Record the stream until it becomes unavailable."""
    print(f"[{now_local()}] Waiting for stream availability...")
    logger.info(f"Waiting for stream availability for {service_type}")
    if not wait_for_stream_online(status_url, f"initial_{service_type}"):
        return

    timestamp = recording_timestamp()
    output_file = f"{output_dir}/recording_{timestamp}.mp3"
    print(f"[{now_local()}] Stream online! Starting recording to {output_file}")
    logger.info(f"Stream online! Starting recording to {output_file}")
    try:
        process_main = run_ffmpeg(service_type, url, os.path.splitext(output_file)[0], "mp3")
    except Exception as e:
        logger.error(f"FAILED TO START RECORDING for {service_type} ({url}): {e}", exc_info=e)
        print(f"[{now_local()}] ERROR: ffmpeg failed to start for {service_type} — recording aborted. See app.log for details.")
        return
    logger.info(f"Started ffmpeg process with PID {process_main.pid} for recording.")
    notify_telegram(f"Recording started at {timestamp}.")
    # Monitor stream; stop when unavailable
    monitor_stream_while_recording(status_url, f"{service_type}_recording")
    print(f"[{now_local()}] Stream stopped. Ending recording.")
    stop_process(process_main)

    logger.info(f"Stream stopped. Ending recording of {output_file}.")
    notify_telegram_file(output_file, caption=f"Recording finished: {output_file}")
    queue_transcription(output_file, transcription_dir)

    if has_sunday_school:
        notify_telegram("Sunday morning recording finished. Starting next recording after sunday school")
        if not wait_for_stream_online(status_url, "sunday_school_break"):
            return

        timestamp = recording_timestamp()
        output_file = f"{output_dir}/recording_{timestamp}.mp3"
        
        try:
            process_sunday_school = run_ffmpeg(service_type, url, os.path.splitext(output_file)[0], "mp3")
        except Exception as e:
            logger.error(f"FAILED TO START SUNDAY SCHOOL RECORDING for {service_type} ({url}): {e}", exc_info=e)
            print(f"[{now_local()}] ERROR: ffmpeg failed to start for sunday school — recording aborted. See app.log for details.")
            return
        logger.info(f"Started ffmpeg process with PID {process_sunday_school.pid} for recording.")
        monitor_stream_while_recording(status_url, "sunday_school_recording")
        print(f"[{now_local()}] Stream stopped. Ending recording.")
        stop_process(process_sunday_school)
        logger.info(f"Stream stopped. Ending recording of {output_file}.")
        notify_telegram_file(output_file, caption=f"Recording finished: {output_file}")
        queue_transcription(output_file, transcription_dir)
    
def run_ffmpeg(name, url, output_path, output_format="mp3"):
    """Run an FFmpeg process and return the process handle."""
    cmd = [
        "ffmpeg", "-y",
        "-nostats", "-loglevel", "warning",
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
        .replace("cong", "congregation").replace("-", "_")
    )
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

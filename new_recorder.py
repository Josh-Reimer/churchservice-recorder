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
from threading import Thread
import atexit
import psutil
import queue
# Load config
with open("config/streams.yml", "r") as f:
    streams_config = yaml.safe_load(f)

load_dotenv()
TIMEZONE = os.getenv("TIMEZONE", "UTC")
local_tz = pytz.timezone(TIMEZONE)
os.environ["TZ"] = TIMEZONE
time.tzset()

print("Schedules from streams.yml:")
print(f"Local timezone: {TIMEZONE}\n")

def now_local():
    return datetime.now(local_tz)


def convert_time(t, stream_tz, service_date=None):
    if not t or t.strip().upper() == "N/A":
        return None
    dt = datetime.strptime(t, "%H:%M")
    service_date = service_date or datetime.now(stream_tz).date()
    naive_service_time = datetime.combine(service_date, dt.time())
    try:
        localized = stream_tz.localize(naive_service_time, is_dst=None)
    except pytz.NonExistentTimeError:
        localized = stream_tz.localize(naive_service_time + timedelta(hours=1), is_dst=True)
    except pytz.AmbiguousTimeError:
        localized = stream_tz.localize(naive_service_time, is_dst=False)
    return localized.astimezone(local_tz)

services = []
class StreamInfo:
    def __init__(self, name, url, status_url, timezone, stream_tz, morning_time_text, evening_time_text, morning_time, evening_time, audio_dir, transcription_dir, sunday_school_break=False):
        self.name = name
        self.url = url
        self.status_url = status_url
        self.timezone = timezone
        self.stream_tz = stream_tz
        self.morning_time_text = morning_time_text
        self.evening_time_text = evening_time_text
        self.morning_time = morning_time
        self.evening_time = evening_time
        self.audio_dir = audio_dir
        self.transcription_dir = transcription_dir
        self.sunday_school_break = sunday_school_break
        self.last_fired = {}  # service_type -> date last fired
       

OUTPUT_DIR = "./recordings"
TRANSCRIPTIONS_DIR = "./transcriptions"
transcription_queue = queue.Queue(maxsize=0)
_transcription_worker = None

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
_executor = ThreadPoolExecutor(max_workers=16)

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
        logger.warning(f"Status check failed (treating as unknown): {e}")
        return None

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


def record_stream(service_type, url, status_url, output_dir, transcription_dir, has_sunday_school=False):
    """Record the stream until it becomes unavailable."""
    print(f"[{now_local()}] Waiting for stream availability...")
    logger.info(f"Waiting for stream availability for {service_type}")
    offline_seconds = 0
    while True:
        available = stream_available(status_url)
        if available is True:
            break
        elif available is False:
            offline_seconds += CHECK_INTERVAL
            print(f"[{now_local()}] Stream offline. Checking again in {CHECK_INTERVAL} seconds...")
            logger.info(f"Stream offline. Checking again in {CHECK_INTERVAL} seconds...")
            if offline_seconds >= CHECK_TIMEOUT * 60:
                print(f"[{now_local()}] Stream did not become available within {CHECK_TIMEOUT} minutes. Exiting.")
                logger.warning(f"Stream did not become available within {CHECK_TIMEOUT} minutes. Exiting.")
                if TELEGRAM_ENABLED:
                    Thread(target=send_telegram_message, args=(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, f"Stream did not become available within {CHECK_TIMEOUT} minutes. Exiting.")).start()
                return
        else:
            logger.warning(f"Status check failed for {status_url}, retrying in {CHECK_INTERVAL} seconds...")
        time.sleep(CHECK_INTERVAL)

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
    if TELEGRAM_ENABLED:
        Thread(target=send_telegram_message, args=(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, f"Recording started at {timestamp}.")).start()
    # Monitor stream; stop when unavailable

    consecutive_offline = 0
    while True:
        available = stream_available(status_url)
        if available is True:
            consecutive_offline = 0
        elif available is False:
            consecutive_offline += 1
            if consecutive_offline >= MAX_OFFLINE_POLLS:
                break
        # None (error): keep recording, don't penalise consecutive count
        time.sleep(CHECK_INTERVAL)

    print(f"[{now_local()}] Stream stopped. Ending recording.")
    stop_process(process_main)

    logger.info(f"Stream stopped. Ending recording of {output_file}.")
    if TELEGRAM_ENABLED:
        send_telegram_file(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, output_file, caption=f"Recording finished: {output_file}")
    if _transcription_worker and _transcription_worker.is_alive():
        transcription_queue.put([output_file, transcription_dir])
        logger.info(f"Queued {output_file} for transcription.")
    else:
        logger.error(f"Transcription worker is not running — {output_file} will NOT be transcribed.")

    if service_type == "sunday_morning" and has_sunday_school:
        if TELEGRAM_ENABLED:
            Thread(target=send_telegram_message, args=(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, "Sunday morning recording finished. Starting next recording after sunday school")).start()
        offline_seconds = 0
        while True:
            available = stream_available(status_url)
            if available is True:
                break
            elif available is False:
                offline_seconds += CHECK_INTERVAL
                print(f"[{now_local()}] Stream offline. Checking again in {CHECK_INTERVAL} seconds...")
                logger.info(f"Stream offline after Sunday morning. Checking again in {CHECK_INTERVAL} seconds...")
                if offline_seconds >= CHECK_TIMEOUT * 60:
                    print(f"[{now_local()}] Stream did not return within {CHECK_TIMEOUT} minutes. Exiting.")
                    logger.warning(f"Stream did not return within {CHECK_TIMEOUT} minutes after Sunday morning. Exiting.")
                    if TELEGRAM_ENABLED:
                        Thread(target=send_telegram_message, args=(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, f"Stream did not return within {CHECK_TIMEOUT} minutes after Sunday morning. Exiting.")).start()
                    return
            else:
                logger.warning(f"Status check failed for {status_url}, retrying in {CHECK_INTERVAL} seconds...")
            time.sleep(CHECK_INTERVAL)

        timestamp = recording_timestamp()
        output_file = f"{output_dir}/recording_{timestamp}.mp3"
        
        try:
            process_sunday_school = run_ffmpeg(service_type, url, os.path.splitext(output_file)[0], "mp3")
        except Exception as e:
            logger.error(f"FAILED TO START SUNDAY SCHOOL RECORDING for {service_type} ({url}): {e}", exc_info=e)
            print(f"[{now_local()}] ERROR: ffmpeg failed to start for sunday school — recording aborted. See app.log for details.")
            return
        logger.info(f"Started ffmpeg process with PID {process_sunday_school.pid} for recording.")
        consecutive_offline = 0
        while True:
            available = stream_available(status_url)
            if available is True:
                consecutive_offline = 0
            elif available is False:
                consecutive_offline += 1
                if consecutive_offline >= MAX_OFFLINE_POLLS:
                    break
            # None (error): keep recording, don't penalise consecutive count
            time.sleep(CHECK_INTERVAL)
        print(f"[{now_local()}] Stream stopped. Ending recording.")
        stop_process(process_sunday_school)
        logger.info(f"Stream stopped. Ending recording of {output_file}.")
        if TELEGRAM_ENABLED:
            send_telegram_file(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, output_file, caption=f"Recording finished: {output_file}")

        if _transcription_worker and _transcription_worker.is_alive():
            transcription_queue.put([output_file, transcription_dir])
            logger.info(f"Queued {output_file} for transcription.")
        else:
            logger.error(f"Transcription worker is not running — {output_file} will NOT be transcribed.")
    
def run_ffmpeg(name, url, output_path, output_format="mp3"):
    """Run an FFmpeg process and return the process handle."""
    cmd = [
        "ffmpeg", "-y",
        "-i", url,
        "-c", "copy",
        f"{output_path}.{output_format}"
    ]
    logger.info(f"Starting FFmpeg for {name}: {url} → {output_path}.{output_format}")

    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        logger.info(f"FFmpeg process started (PID {process.pid})")
        return process  # <— Return immediately
    except Exception as e:
        logger.error(f"Error running FFmpeg for {name}: {e}")
        raise

def gpu_transcribe_worker():
    import whisper
    import gc
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info(f"Loading Whisper model on {device}")

    # Load the model once and keep it resident for all queued files.
    # This avoids reloading (minutes of overhead) between recordings.
    try:
        # Use the local model file bundled in the image (COPY models models in Dockerfile).
        # This avoids a ~3 GB download on every container start.
        local_model_path = "/app/models/large-v3.pt"
        model_name_or_path = local_model_path if os.path.exists(local_model_path) else "large-v3"
        model = whisper.load_model(model_name_or_path, device=device)
    except Exception as e:
        logger.error(f"Failed to load Whisper model: {e}")
        return

    try:
        while True:
            # Fetch the [audio_file_path, transcription_dir] pair as a single item.
            # Bug fix: previously called queue.get() twice, causing a deadlock / wrong dir.
            item = transcription_queue.get()
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
                if TELEGRAM_ENABLED:
                    send_telegram_file(
                        TELEGRAM_BOT_TOKEN,
                        TELEGRAM_CHAT_ID,
                        transcription_text_file,
                        caption=f"Transcription finished: {transcription_text_file}"
                    )

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
def threaded(job_func, *args):
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
    """Start the background transcription worker thread."""
    global _transcription_worker
    _transcription_worker = Thread(target=gpu_transcribe_worker, daemon=True)
    _transcription_worker.start()
    logger.info("Transcription worker thread started.")
    return _transcription_worker


def service_time_for_today(stream_info, service_type, today_in_stream_tz):
    time_text = (
        stream_info.morning_time_text
        if service_type == "sunday_morning"
        else stream_info.evening_time_text
    )
    return convert_time(time_text, stream_info.stream_tz, today_in_stream_tz)


def maybe_start_service(stream_info, service_type, scheduled_time, current_time):
    if scheduled_time is None:
        return

    service_date = scheduled_time.date()
    if stream_info.last_fired.get(service_type) == service_date:
        return

    if scheduled_time <= current_time < scheduled_time + SERVICE_TRIGGER_WINDOW:
        stream_info.last_fired[service_type] = service_date
        threaded(
            record_stream,
            service_type,
            stream_info.url,
            stream_info.status_url,
            stream_info.audio_dir,
            stream_info.transcription_dir,
            stream_info.sunday_school_break,
        )
        logger.info(f"Triggered {stream_info.name} {service_type} at {current_time}; scheduled for {scheduled_time}")
    elif current_time > scheduled_time + SERVICE_TRIGGER_WINDOW:
        logger.warning(f"Missed trigger window for {stream_info.name} {service_type} scheduled at {scheduled_time} — container may have started late.")
        stream_info.last_fired[service_type] = service_date  # suppress repeat warnings


def check_services():
    current_time = now_local()
    for stream_info in services:
        now_in_stream_tz = current_time.astimezone(stream_info.stream_tz)
        if now_in_stream_tz.weekday() != 6:
            continue

        service_date = now_in_stream_tz.date()
        maybe_start_service(
            stream_info,
            "sunday_morning",
            service_time_for_today(stream_info, "sunday_morning", service_date),
            current_time
        )
        maybe_start_service(
            stream_info,
            "sunday_evening",
            service_time_for_today(stream_info, "sunday_evening", service_date),
            current_time
        )




if __name__ == "__main__":
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
    except Exception as e:
        logger.error(f"Error creating output directory {OUTPUT_DIR}: {e}")
    try:
        os.makedirs(TRANSCRIPTIONS_DIR, exist_ok=True)
    except Exception as e:
        logger.error(f"Error creating transcriptions directory {TRANSCRIPTIONS_DIR}: {e}")
    
    seen_urls = set()
    for stream in streams_config.get("streams", []):
        url = stream.get("url", "")
        if url in seen_urls:
            logger.warning(f"Duplicate stream URL '{url}' in streams.yml — skipping.")
            print(f"WARNING: Duplicate stream URL skipped: {url}")
            continue
        seen_urls.add(url)
        name = stream.get("name", "Unknown")
        tz_name = stream.get("timezone", "UTC")
        stream_tz = pytz.timezone(tz_name)

        full_name = stream.get("full_name", stream.get("name", "Unknown"))
        safe_name = full_name.lower().replace(" ", "_").replace(",", "").replace(".", "").replace("cong","congregation").replace("-", "_")
        output_dir = os.path.join(OUTPUT_DIR, safe_name)
        transcription_dir = os.path.join("./transcriptions", safe_name)
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(transcription_dir, exist_ok=True)

        morning_time_text = stream.get("sunday_morning_service_time")
        evening_time_text = stream.get("sunday_evening_service_time")
        sunday_school_break = stream.get("sunday_school_break", False)

        morning_dt_object = convert_time(morning_time_text, stream_tz)
        evening_dt_object = convert_time(evening_time_text, stream_tz)

        stream_info = StreamInfo(
            name=name,
            url=stream.get("url", ""),
            status_url=stream.get("status_url", ""),
            timezone=tz_name,
            stream_tz=stream_tz,
            morning_time_text=morning_time_text,
            evening_time_text=evening_time_text,
            morning_time=morning_dt_object,
            evening_time=evening_dt_object,
            audio_dir=output_dir,
            transcription_dir=transcription_dir,
            sunday_school_break=sunday_school_break,
        )
        services.append(stream_info)

        if stream_info.morning_time is not None:
            safe_name = stream_info.name.lower().replace(" ", "_").replace(",", "").replace(".", "").replace("cong","congregation").replace("-", "_")

            output_dir_for_svc = os.path.join(OUTPUT_DIR, safe_name)

            os.makedirs(output_dir_for_svc, exist_ok=True)

            print(f"Scheduled {stream_info.name} sunday_morning at {stream_info.morning_time} -> {output_dir_for_svc}")
            
            
            logger.info(f"Scheduled {stream_info.name} sunday_morning at {stream_info.morning_time} -> {output_dir_for_svc}")

        if stream_info.evening_time is not None:
            safe_name = stream_info.name.lower().replace(" ", "_").replace(",", "").replace(".", "").replace("cong","congregation").replace("-", "_")

            output_dir_for_svc = os.path.join(OUTPUT_DIR, safe_name)

            os.makedirs(output_dir_for_svc, exist_ok=True)

            print(f"Scheduled {stream_info.name} sunday_evening at {stream_info.evening_time} -> {output_dir_for_svc}")
            logger.info(f"Scheduled {stream_info.name} sunday_evening at {stream_info.evening_time} -> {output_dir_for_svc}")



        print(f"Stream: {name}")
        print(f"  Timezone: {tz_name}")
        print(f"  Sunday Morning Service (your time): {morning_dt_object}")
        print(f"  Sunday Evening Service (your time): {evening_dt_object}")
        print()

    # Start the transcription worker so queued files are processed as they arrive.
    schedule_transcriptions()
    schedule.every(CHECK_INTERVAL).seconds.do(check_services)

    logger.info("Entering schedule loop. Waiting for Sunday services...")
    print("Recorder running. Waiting for scheduled services (Ctrl+C to stop).")
    while True:
        schedule.run_pending()
        time.sleep(1)

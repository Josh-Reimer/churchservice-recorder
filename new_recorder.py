import subprocess
import time
import requests
import schedule
from datetime import datetime
from dotenv import load_dotenv
import os
import tzlocal
import pytz
import logging
from logging.handlers import TimedRotatingFileHandler
import yaml
from concurrent.futures import ThreadPoolExecutor
import subprocess
import signal
from threading import Thread
import atexit
import psutil
# Load config
with open("config/streams.yml", "r") as f:
    streams_config = yaml.safe_load(f)

# Load environment timezone (fallback: UTC)
load_dotenv()
TIMEZONE = os.getenv("TIMEZONE", "UTC")
local_tz = pytz.timezone(TIMEZONE)

print("Schedules from streams.yml:")
print(f"Local timezone: {TIMEZONE}\n")

def convert_time(t, stream_tz):
    if not t or t.strip().upper() == "N/A":
        return "N/A"
    dt = datetime.strptime(t, "%H:%M")
    localized = stream_tz.localize(datetime.combine(datetime.now().date(), dt.time()))
    return localized.astimezone(local_tz).strftime("%H:%M")

sunday_morning_services = []
sunday_evening_services = []
services = []

class StreamInfo:
    def __init__(self, name, url,status_url, timezone, morning_time, evening_time):
        self.name = name
        self.url = url
        self.status_url = status_url
        self.timezone = timezone
        self.morning_time = morning_time
        self.evening_time = evening_time

for stream in streams_config.get("streams", []):
    name = stream.get("name", "Unknown")
    tz_name = stream.get("timezone", "UTC")
    stream_tz = pytz.timezone(tz_name)

    morning_str = convert_time(stream.get("sunday_morning_service_time"), stream_tz)
    sunday_morning_services.append((name, morning_str))
   
    evening_str = convert_time(stream.get("sunday_evening_service_time"), stream_tz)
    sunday_evening_services.append((name, evening_str))

    stream_info = StreamInfo(
        name=name,
        url=stream.get("url", ""),
        status_url=stream.get("status_url", ""),
        timezone=tz_name,
        morning_time=morning_str,
        evening_time=evening_str
    )
    services.append(stream_info)

    print(f"Stream: {name}")
    print(f"  Timezone: {tz_name}")
    print(f"  Sunday Morning Service (your time): {morning_str}")
    print(f"  Sunday Evening Service (your time): {evening_str}")
    print()


for stream in streams_config.get("streams", []):
    url = stream.get("url")
    #print(f"Checking stream URL: {url}")

# Create timed rotating handler
handler = TimedRotatingFileHandler(
    'app.log',
    when='D',     # Rotate at midnight
    interval=14,          # Every 14 days
    backupCount=365,       # Keep 365 days worth
    atTime=None,         # At midnight (default)
    utc=False           # Use local time
)

# Set format with date
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(handler)

logger.info("This will rotate daily at midnight")

load_dotenv()
TIMEZONE = os.getenv("TIMEZONE")
LOCAL_TZ = pytz.timezone(TIMEZONE)  # Replace with your actual timezone
os.environ["TZ"] = f"{TIMEZONE}"  # Set your timezone here
time.tzset()
#STREAM_URL = "http://localhost:8000/dublab"
STREAM_URL = os.getenv("STREAM_URL")
STREAM_STATUS_URL = os.getenv("STREAM_STATUS_URL")
CHECK_INTERVAL = 30  # seconds
OUTPUT_DIR = "./recordings"




CONGREGATION_OUTPUT_DIRS = []
CONGREGATION_TRANSCRIPTIONS_DIRS = []

for stream in streams_config.get("streams", []):
    full_name = stream.get("full_name", stream.get("name", "Unknown"))
    safe_name = full_name.lower().replace(" ", "_").replace(",", "").replace(".", "").replace("cong","congregation").replace("-", "_")
    output_dir = os.path.join(OUTPUT_DIR, safe_name)
    CONGREGATION_OUTPUT_DIRS.append((full_name, output_dir))
    transcription_dir = os.path.join("./transcriptions", safe_name)
    CONGREGATION_TRANSCRIPTIONS_DIRS.append((full_name, transcription_dir))
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(transcription_dir, exist_ok=True)

#print(CONGREGATION_OUTPUT_DIRS)

TRANSCRIPTIONS_DIR = "./transcriptions"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CHECK_TIMEOUT = 90 # minutes

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
            print("Stream is online.")
            logger.info("Stream is online.")
            return True
        else:
            print("Stream is offline.")
            logger.info("Stream is offline.")
            return False
    except requests.RequestException as e:
        print(f"Error checking stream status: {e}")
        logger.error(f"Error checking stream status: {e}")

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
        logger.error(f"Error sending Telegram message: {e}")
        return {"ok": False, "error": str(e)}


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


def record_stream(service_type, url,status_url, output_dir=OUTPUT_DIR):
    """Record the stream until it becomes unavailable."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    output_file = f"{OUTPUT_DIR}/recording_{timestamp}.mp3"

    print(f"[{datetime.now()}] Waiting for stream availability...")
    logger.info(f"Waiting for stream availability for {service_type}")
    total_time = 0
    while not stream_available(status_url):
        print(f"[{datetime.now()}] Stream offline. Checking again in {CHECK_INTERVAL} seconds...")
        total_time += CHECK_INTERVAL
        logger.info(f"Stream offline. Checking again in {CHECK_INTERVAL} seconds...")
        if total_time >= CHECK_TIMEOUT * 60:
            print(f"[{datetime.now()}] Stream did not become available within {CHECK_TIMEOUT} minutes. Exiting.")
            logger.warning(f"Stream did not become available within {CHECK_TIMEOUT} minutes. Exiting.")
            Thread(target=send_telegram_message, args=(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, f"Stream did not become available within {CHECK_TIMEOUT} minutes. Exiting.")).start()
            return
        
        time.sleep(CHECK_INTERVAL)

    print(f"[{datetime.now()}] Stream online! Starting recording to {output_file}")
    logger.info(f"Stream online! Starting recording to {output_file}")
    Thread(target=send_telegram_message, args=(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, f"Recording started at {timestamp}.")).start()
    # Run ffmpeg until the stream drops
    process_main = run_ffmpeg(service_type, url, output_file.replace(".mp3",""), "mp3")
    logger.info(f"Started ffmpeg process with PID {process_main.pid} for recording.")
    # Monitor stream; stop when unavailable
    while stream_available(status_url):
        time.sleep(CHECK_INTERVAL)
    print(f"[{datetime.now()}] Stream stopped. Ending recording.")
    process_main.terminate()
    try:
        process_main.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process_main.kill()

    logger.info(f"Stream stopped. Ending recording of {output_file}.")
    send_telegram_file(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, output_file, caption=f"Recording finished: {output_file}")
    try:
        transcribe_audio(output_file)
    except Exception as e:
        logger.error(f"Error during transcription: {e}")
    logger.info(f"Transcription completed for {output_file}.")

    if service_type == "sunday_morning":    # After the Sunday morning recording ends, start checking for stream again because i guess they pause the stream for sunday school
        Thread(target=send_telegram_message, args=(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, "Sunday morning recording finished. Starting next recording after sunday school")).start()
        while not stream_available(status_url):
            print(f"[{datetime.now()}] Stream offline. Checking again in {CHECK_INTERVAL} seconds...")
            time.sleep(CHECK_INTERVAL)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        output_file = f"{OUTPUT_DIR}/recording_{timestamp}.mp3"
        # Run ffmpeg until the stream drops
        process_sunday_school = run_ffmpeg(service_type, url, output_file.replace(".mp3",""), "mp3")
        logger.info(f"Started ffmpeg process with PID {process_sunday_school.pid} for recording.")
        while stream_available(status_url):
            time.sleep(CHECK_INTERVAL)
        print(f"[{datetime.now()}] Stream stopped. Ending recording.")
        logger.info(f"Stream stopped. Ending recording of {output_file}.")
        send_telegram_file(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, output_file, caption=f"Recording finished: {output_file}")
        try:
            transcribe_audio(output_file)
        except Exception as e:
            logger.error(f"Error during transcription: {e}")
        logger.info(f"Transcription completed for {output_file}.")
    if service_type == "sunday_morning" and 'process_sunday_school' in locals():
        process_sunday_school.terminate()
        try:
            process_sunday_school.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process_sunday_school.kill()

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

def transcribe_audio(file_path):
    import whisper
    import gc
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info(f"Loading Whisper model on {device}")
    try:
        model = whisper.load_model("large", device=device)
        # use fp16 on CUDA to reduce memory & speed up
        result = model.transcribe(file_path, fp16=(device == "cuda"))
        text = result.get("text", "")
        print(text)

        transciption_text_file = os.path.join(
            TRANSCRIPTIONS_DIR, os.path.basename(file_path).replace(".mp3", ".txt")
        )
        with open(transciption_text_file, "w", encoding="utf-8") as f:
            f.write(text)

        send_telegram_file(
            TELEGRAM_BOT_TOKEN,
            TELEGRAM_CHAT_ID,
            transciption_text_file,
            caption=f"Transcription finished: {transciption_text_file}"
        )

    except Exception as e:
        logger.error(f"Transcription error for {file_path}: {e}")
        raise
    finally:
        try:
            del model
            if device == "cuda":
                torch.cuda.empty_cache()
        except Exception:
            pass
        gc.collect()
def kill_ffmpeg_children():
    for proc in psutil.process_iter(['pid', 'name']):
        if 'ffmpeg' in proc.info['name']:
            try:
                proc.terminate()
            except psutil.NoSuchProcess:
                pass

atexit.register(kill_ffmpeg_children)
def threaded(job_func, *args):
    executor = ThreadPoolExecutor(max_workers=8)
    executor.submit(job_func, *args)

def schedule_recordings():
    """Schedule recordings based on streams_config."""

    #TODO: Add more schedules from streams_config
    for svc in services:
        # schedule using StreamInfo objects
        if svc.morning_time and svc.morning_time != "N/A":
            safe_name = svc.name.lower().replace(" ", "_").replace(",", "").replace(".", "").replace("cong","congregation").replace("-", "_")
            output_dir_for_svc = os.path.join(OUTPUT_DIR, safe_name)
            os.makedirs(output_dir_for_svc, exist_ok=True)
            schedule.every().sunday.at(svc.morning_time).do(threaded,record_stream, "sunday_morning", svc.url,svc.status_url, output_dir_for_svc)
            print(f"Scheduled {svc.name} sunday_morning at {svc.morning_time} -> {output_dir_for_svc}")
            logger.info(f"Scheduled {svc.name} sunday_morning at {svc.morning_time} -> {output_dir_for_svc}")

        if svc.evening_time and svc.evening_time != "N/A":
            safe_name = svc.name.lower().replace(" ", "_").replace(",", "").replace(".", "").replace("cong","congregation").replace("-", "_")
            output_dir_for_svc = os.path.join(OUTPUT_DIR, safe_name)
            os.makedirs(output_dir_for_svc, exist_ok=True)
            schedule.every().sunday.at(svc.evening_time).do(threaded,record_stream, "sunday_evening", svc.url,svc.status_url, output_dir_for_svc)
            print(f"Scheduled {svc.name} sunday_evening at {svc.evening_time} -> {output_dir_for_svc}")
            logger.info(f"Scheduled {svc.name} sunday_evening at {svc.evening_time} -> {output_dir_for_svc}")

    print(schedule.get_jobs())
    logger.info(f"Scheduled jobs: {schedule.get_jobs()}")
    print(STREAM_STATUS_URL)
    print(f"Using timezone: {TIMEZONE}")
    print(STREAM_URL)

    print(f"[{datetime.now()}] Scheduler started. Waiting for recording times...")
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
    except Exception as e:
        logger.error(f"Error creating output directory {OUTPUT_DIR}: {e}")
    try:
        os.makedirs(TRANSCRIPTIONS_DIR, exist_ok=True)
    except Exception as e:
        logger.error(f"Error creating transcriptions directory {TRANSCRIPTIONS_DIR}: {e}")
    schedule_recordings()
    
import subprocess
import time
import requests
import schedule
from datetime import datetime
from dotenv import load_dotenv
import os
import tzlocal
import pytz

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
TRANSCRIPTIONS_DIR = "./transcriptions"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def stream_available():
    """Check stream status from external API and print status."""
    url = STREAM_STATUS_URL
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        if data.get("full") == 1:
            print("This system is full.")
        elif not data.get("autoscale") and data.get("percentage", 0) > 74:
            print(f"This system is {round(data.get('percentage', 0))}% full.")
            print(f"Status message: {data.get('message')}")
        if data.get("status") == 1:
            print("Stream is online.")
            return True
        else:
            print("Stream is offline.")
            return False
    except requests.RequestException as e:
        print(f"Error checking stream status: {e}")

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
        return response.json()
    except requests.RequestException as e:
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
            return response.json()
    except FileNotFoundError:
        return {"ok": False, "error": f"File not found: {file_path}"}
    except requests.RequestException as e:
        return {"ok": False, "error": str(e)}


def record_stream(service_type):
    """Record the stream until it becomes unavailable."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    output_file = f"{OUTPUT_DIR}/recording_{timestamp}.mp3"

    print(f"[{datetime.now()}] Waiting for stream availability...")
    while not stream_available():
        print(f"[{datetime.now()}] Stream offline. Checking again in {CHECK_INTERVAL} seconds...")
        time.sleep(CHECK_INTERVAL)

    print(f"[{datetime.now()}] Stream online! Starting recording to {output_file}")
    send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, f"Recording started at {timestamp}.")

    # Run ffmpeg until the stream drops
    process = subprocess.Popen([
        "ffmpeg", "-y",
        "-i", STREAM_URL,
        "-c", "copy",
        output_file
    ])

    # Monitor stream; stop when unavailable
    while stream_available():
        time.sleep(CHECK_INTERVAL)

    print(f"[{datetime.now()}] Stream stopped. Ending recording.")
    send_telegram_file(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, output_file, caption=f"Recording finished: {output_file}")
    transcribe_audio(output_file)

    if service_type == "sunday_morning":    # After the Sunday morning recording ends, start checking for stream again because i guess they pause the stream for sunday school
        send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, "Sunday morning recording finished. Starting next recording after sunday school")
        while not stream_available():
            print(f"[{datetime.now()}] Stream offline. Checking again in {CHECK_INTERVAL} seconds...")
            time.sleep(CHECK_INTERVAL)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        output_file = f"{OUTPUT_DIR}/recording_{timestamp}.mp3"
        # Run ffmpeg until the stream drops
        process = subprocess.Popen([
            "ffmpeg", "-y",
            "-i", STREAM_URL,
            "-c", "copy",
            output_file
        ])
        while stream_available():
            time.sleep(CHECK_INTERVAL)
        print(f"[{datetime.now()}] Stream stopped. Ending recording.")
        send_telegram_file(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, output_file, caption=f"Recording finished: {output_file}")
        transcribe_audio(output_file)

    process.terminate()
    process.wait()

def transcribe_audio(file_path):
    import whisper

    model = whisper.load_model("large")  # Change to "tiny", "small", "medium", or "large" as needed
    result = model.transcribe(file_path)
    print(result["text"])
    transciption_text_file = os.path.join(TRANSCRIPTIONS_DIR, os.path.basename(file_path).replace(".mp3", ".txt"))
    with open(transciption_text_file, "w", encoding="utf-8") as f:
        f.write(result["text"])
    send_telegram_file(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, transciption_text_file, caption=f"Transcription finished: {transciption_text_file}")

def schedule_recordings():
    """Schedule Sunday recordings at 10:00 and 18:00."""
    schedule.every().sunday.at("10:00").do(record_stream,"sunday_morning")
   
    schedule.every().sunday.at("18:30").do(record_stream,"sunday_evening")
    schedule.every().tuesday.at("19:00").do(record_stream,"tuesday_youth_evening")
    print(schedule.get_jobs())


    print(f"[{datetime.now()}] Scheduler started. Waiting for recording times...")
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    schedule_recordings()

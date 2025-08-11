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

def record_stream():
    """Record the stream until it becomes unavailable."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    output_file = f"{OUTPUT_DIR}/recording_{timestamp}.mp3"

    print(f"[{datetime.now()}] Waiting for stream availability...")
    while not stream_available():
        print(f"[{datetime.now()}] Stream offline. Checking again in {CHECK_INTERVAL} seconds...")
        time.sleep(CHECK_INTERVAL)

    print(f"[{datetime.now()}] Stream online! Starting recording to {output_file}")

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
    process.terminate()
    process.wait()

def schedule_recordings():
    """Schedule Sunday recordings at 10:00 and 18:00."""
    schedule.every().sunday.at("10:00").do(record_stream)
    schedule.every().sunday.at("18:00").do(record_stream)
    schedule.every().monday.at("17:00").do(record_stream)
    schedule.every().tuesday.at("17:00").do(record_stream)
    schedule.every().wednesday.at("17:00").do(record_stream)
    schedule.every().thursday.at("17:00").do(record_stream)
   
    

    print(f"[{datetime.now()}] Scheduler started. Waiting for recording times...")
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    schedule_recordings()

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
LOCAL_TZ = pytz.timezone("your timezone here")  # Replace with your actual timezone
os.environ["TZ"] = "your timezone here"
time.tzset()
#STREAM_URL = "http://localhost:8000/dublab"
STREAM_URL = os.getenv("STREAM_URL")
CHECK_INTERVAL = 30  # seconds
OUTPUT_DIR = "./recordings"

def stream_available():
    """Check if the Icecast stream is available."""
    try:
        r = requests.get(STREAM_URL, stream=True, timeout=5)
        return r.status_code == 200
    except requests.RequestException:
        return False

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

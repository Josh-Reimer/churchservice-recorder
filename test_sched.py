from datetime import datetime, timedelta
import pytz
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
import subprocess
from threading import Thread
import atexit
import psutil

CHECK_WINDOW = timedelta(minutes=1)

# Load config
with open("config/now.yml", "r") as f:
    streams_config = yaml.safe_load(f)

# Load environment timezone (fallback: UTC)
load_dotenv()
TIMEZONE = os.getenv("TIMEZONE", "UTC")
local_tz = pytz.timezone(TIMEZONE)


def convert_time(t, stream_tz):
    if not t or t.strip().upper() == "N/A":
        return "N/A"
    dt = datetime.strptime(t, "%H:%M")
    localized = stream_tz.localize(datetime.combine(datetime.now().date(), dt.time()))
    return localized.astimezone(local_tz)

sunday_morning_services = []
sunday_evening_services = []
services = []

last_recording_ending = None   # should actually be the time the last recording ends, not the config time ending
last_hours = []
class StreamInfo:
    def __init__(self, name, url,status_url, timezone, morning_time, evening_time, audio_dir, transcription_dir):
        self.name = name
        self.url = url
        self.status_url = status_url
        self.timezone = timezone
        self.morning_time = morning_time
        self.evening_time = evening_time
        self.audio_dir = audio_dir
        self.transcription_dir = transcription_dir
        self._last_stream = set()
        self._fired= set()

    def has_fired(self, service_type):
        return service_type in self._fired

    def mark_fired(self, service_type):
        self._fired.add(service_type)
    def mark_is_last_stream(self, last_stream):
        self._last_stream = last_stream
    def is_last_stream(self):
        return self.is_last_stream

OUTPUT_DIR = "./recordings"
TRANSCRIPTIONS_DIR = "./transcriptions"

for stream in streams_config.get("streams", []):
    name = stream.get("name", "Unknown")
    tz_name = stream.get("timezone", "UTC")
    stream_tz = pytz.timezone(tz_name)

    full_name = stream.get("full_name", stream.get("name", "Unknown"))
    safe_name = full_name.lower().replace(" ", "_").replace(",", "").replace(".", "").replace("cong","congregation").replace("-", "_")
    output_dir = os.path.join(OUTPUT_DIR, safe_name)
    transcription_dir = os.path.join("./transcriptions", safe_name)
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(transcription_dir, exist_ok=True)

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
        evening_time=evening_str,
        audio_dir = output_dir,
        transcription_dir = transcription_dir
    )

    services.append(stream_info)

    last_hours.append(evening_str if evening_str != "N/A" else 0)
    

last_recording_starts = max(last_hours)

def stream_available(status_url):
    """Check stream status from external API and print status."""
    url = status_url
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


def check_services():
    """
    Periodically called function that decides
    whether any service should start recording.
    """
    now_utc = datetime.now(pytz.utc)
    print(f'checking service {now_utc}')
    for service in services:
        check_service_time(
            service=service,
            now=now_utc,
            service_type="sunday_morning",
            scheduled_time=service.morning_time
        )
        
        check_service_time(
            service=service,
            now=now_utc,
            service_type="sunday_evening",
            scheduled_time=service.evening_time
        )

        if not stream_available(service.status_url) and service._fired:
            print('stream finished')

def check_service_time(service, now, service_type, scheduled_time):
    """
    Decide whether a single service should fire.
    """
    if scheduled_time is None:
        return

    if service.has_fired(service_type):
        return

    if not is_within_trigger_window(now, scheduled_time):
        return
    if stream_available(service.status_url):
        trigger_recording(service, service_type)
        service.mark_fired(service_type)
        #this block will run after the recording is finished and then will go on to 
        # transcribe the audio


def is_within_trigger_window(now, scheduled_time):
    """
    Returns True if `now` is inside the allowed
    execution window for this service.
    """
    
    return scheduled_time <= now < scheduled_time + CHECK_WINDOW


def trigger_recording(service, service_type):
    """
    Launch the recording job in a background thread.
    """
    threaded(
        record_stream,
        service_type,
        service.url,
        service.status_url,
        service.audio_dir,
        service.transcription_dir
    )

def trigger_transcription(service):
    """
    launch the transcription job on my gpu in a background thread.
    """
    threaded(
        transcribe_audio,
        service.audio_dir,
        service.transcription_dir
    )

schedule.every(30).seconds.do(check_services)

while True:
        schedule.run_pending()
        


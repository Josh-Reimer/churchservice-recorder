import subprocess
import sys
import signal
import os
from dotenv import load_dotenv

load_dotenv()
#STREAM_URL = os.getenv("STREAM_URL")
#STREAM_URL = "https://dublab.out.airtime.pro/dublab_a"
STREAM_URL = "http://localhost:8000/dublab"  # my local test stream
OUTPUT_FILE = "thefile.mp3"

def record(duration=None):
    cmd = [
        "ffmpeg",
        "-y",  # overwrite
        "-i", STREAM_URL,
        "-c:a", "copy",  # no re-encoding
        "-f", "mp3",
        OUTPUT_FILE
    ]

    if duration:
        cmd.insert(2, "-t")
        cmd.insert(3, str(duration))

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    try:
        print("Recording… Press Ctrl+C to stop.")
        process.communicate()
    except KeyboardInterrupt:
        print("Stopping…")
        process.send_signal(signal.SIGINT)
        process.wait()

    print("Recording saved to", OUTPUT_FILE)

if __name__ == "__main__":
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else None
    record(seconds)

import os
import time
import datetime
import subprocess
import schedule
import asyncio
from telegram import Bot
from telegram.constants import ChatAction
from dotenv import load_dotenv
import aiohttp
import threading

# Load .env
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Init bot
bot = Bot(token=TELEGRAM_TOKEN)

# Config
STREAM_URL = os.getenv("STREAM_URL")
#STREAM_URL = "https://dublab.out.airtime.pro/dublab_a"  #test stream

OUTPUT_DIR = "recordings"
SILENCE_DURATION = 20
MAX_DURATION = 2 * 60 * 60
STREAM_CHECK_TIMEOUT = 15 * 60  # 15 minutes
STREAM_CHECK_INTERVAL = 30  # seconds
RETRY_ATTEMPTS = 15
RETRY_DELAY = 5 * 60  # 5 minutes between retries

def generate_filename():
    now = datetime.datetime.now()
    return os.path.join(OUTPUT_DIR, now.strftime("church_%Y-%m-%d_%H-%M.mp3"))


async def send_telegram_message(text):
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text)
        print(f"[telegram] Sent: {text}")
    except Exception as e:
        print(f"[telegram-error] Could not send message: {e}")


async def send_to_telegram(file_path):
    try:
        print(f"[telegram] Sending {file_path}")
        await bot.send_chat_action(chat_id=TELEGRAM_CHAT_ID, action=ChatAction.UPLOAD_AUDIO)
        with open(file_path, "rb") as audio:
            await bot.send_audio(chat_id=TELEGRAM_CHAT_ID, audio=audio)
        print(f"[telegram] File sent")
    except Exception as e:
        print(f"[telegram-error] Failed to send file: {e}")
        await send_telegram_message(f"[error] Could not send recording: {e}")


async def wait_for_stream(url: str, timeout: int = STREAM_CHECK_TIMEOUT, interval: int = STREAM_CHECK_INTERVAL) -> bool:
    print(f"[info] Waiting for stream to become available (timeout {timeout}s)...")
    start = time.time()
    attempt = 0
    while time.time() - start < timeout:
        attempt += 1
        try:
            async with aiohttp.ClientSession() as session:
                async with session.head(url, timeout=5) as resp:
                    if resp.status == 200:
                        print("[info] Stream is now online.")
                        return True
        except Exception as e:
            message = f"[retry #{attempt}] Stream not ready yet: {e}"
            print(message)
            await send_telegram_message(message)
        await asyncio.sleep(interval)
    print("[fail] Stream did not become available in time.")
    await send_telegram_message("❌ Stream did not become available in time.")
    return False


async def async_record_stream():
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        print(f"[attempt {attempt}] Checking stream availability...")
        await send_telegram_message(f"📡 Attempt {attempt} to check stream availability…")
        stream_ready = await wait_for_stream(STREAM_URL)
        if stream_ready:
            break
        if attempt < RETRY_ATTEMPTS:
            print(f"[retry] Waiting {RETRY_DELAY}s before next attempt...")
            await asyncio.sleep(RETRY_DELAY)
    else:
        await send_telegram_message("❌ Stream never came online after multiple attempts. Skipping recording.")
        return

    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        filename = generate_filename()

        print(f"[{datetime.datetime.now()}] Recording to {filename}")
        await send_telegram_message("⛪️ Recording started.")

        cmd = [
            "ffmpeg",
            "-y",
            "-i", STREAM_URL,
            "-af", f"silencedetect=n=-50dB:d={SILENCE_DURATION}",
            "-t", str(MAX_DURATION),
            "-c:a", "libmp3lame",
            "-b:a", "64k",
            filename
        ]

        process = subprocess.Popen(cmd, stderr=subprocess.STDOUT)
        process.wait()

        print(f"[done] Recording finished: {filename}")
        await send_to_telegram(filename)

    except Exception as e:
        print(f"[error] {e}")
        await send_telegram_message(f"[ERROR] Recording failed: {e}")


def record_stream():
    """Run the async recording function in a new event loop"""
    def run_in_thread():
        # Create a new event loop for this thread
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        try:
            new_loop.run_until_complete(async_record_stream())
        except Exception as e:
            print(f"[thread-error] {e}")
        finally:
            new_loop.close()
    
    # Run in a separate thread to avoid blocking the scheduler
    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()


async def send_startup_message():
    """Send startup message using a dedicated event loop"""
    try:
        await send_telegram_message("📦 Recorder container started and waiting…")
    except Exception as e:
        print(f"[startup-error] Could not send startup message: {e}")


def main():
    # Send startup message
    startup_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(startup_loop)
    try:
        startup_loop.run_until_complete(send_startup_message())
    except Exception as e:
        print(f"[startup-error] {e}")
    finally:
        startup_loop.close()

    # Schedule recordings
    schedule.every().sunday.at("10:30").do(record_stream)
    schedule.every().sunday.at("18:00").do(record_stream)
    schedule.every().sunday.at("18:30").do(record_stream)
    schedule.every().tuesday.at("19:00").do(record_stream)

    print("✅ Recorder running. Waiting for scheduled times.")
    
    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
            print(f"[{datetime.datetime.now()}] Still waiting...")
    except KeyboardInterrupt:
        print("\n[info] Shutting down...")
    except Exception as e:
        err = f"[FATAL ERROR] {e}"
        print(err)
        # Send error message in a new event loop
        error_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(error_loop)
        try:
            error_loop.run_until_complete(send_telegram_message(err))
        except:
            pass
        finally:
            error_loop.close()


if __name__ == "__main__":
    main()
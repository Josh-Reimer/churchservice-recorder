from flask import Flask, render_template, request, redirect, url_for, send_from_directory, session, abort
from flask_wtf.csrf import CSRFProtect
import os
import yaml
import pytz
import mutagen
from datetime import datetime
from mutagen.mp3 import MP3

app = Flask(__name__)
app.secret_key = "8888mi3o8q4u qci9jdq9w8edjj+)L!@))LAZ)@}A~?>ncxm3x8932md29dmk"  # Needed for sessions

# Enable CSRF protection
csrf = CSRFProtect(app)

# Simple login credentials
USERNAME = "admin"
PASSWORD = "42"

RECORDINGS_FOLDER = "/app/recordings"
TRANSCRIPTIONS_FOLDER = "/app/transcriptions"
CONFIG_FILE = "/app/config/streams.yml"

COMMON_TIMEZONES = [
    "America/St_Johns", "America/Halifax", "America/New_York",
    "America/Chicago", "America/Winnipeg", "America/Regina",
    "America/Denver", "America/Phoenix", "America/Los_Angeles",
    "America/Mexico_City", "America/Dawson_Creek",
    "America/North_Dakota/Beulah", "UTC",
]

DAYS_LIST = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]

def load_streams():
    with open(CONFIG_FILE, "r") as f:
        return yaml.safe_load(f) or {"streams": []}

def save_streams(data):
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

def _require_login():
    """Check that user is logged in, abort with 403 if not."""
    if not session.get("logged_in"):
        abort(403)

def _load_and_validate_stream_index(idx):
    """Load streams and validate that idx is in bounds. Returns (data, streams) or aborts."""
    data = load_streams()
    streams = data.get("streams", [])
    if idx < 0 or idx >= len(streams):
        abort(404)
    return data, streams

def _build_stream_dict_from_form():
    """Extract stream dict from POST form data. Aborts 400 on invalid input."""
    timezone = request.form.get("timezone", "UTC").strip()
    if timezone not in pytz.all_timezones_set:
        abort(400, description=f"Unknown timezone: {timezone!r}. Use an IANA name like America/Chicago.")
    return {
        "name": request.form.get("name", "").strip(),
        "full_name": request.form.get("full_name", "").strip(),
        "url": request.form.get("url", "").strip(),
        "status_url": request.form.get("status_url", "").strip(),
        "timezone": timezone,
        "services": _parse_slots_from_form(),
        "enabled": request.form.get("enabled") == "on",
    }

def _normalize_stream(stream):
    """Ensure stream has a `services` list, migrating legacy sunday_* fields if needed."""
    if "services" not in stream:
        morning = stream.get("sunday_morning_service_time")
        evening = stream.get("sunday_evening_service_time")
        ssb = stream.get("sunday_school_break", False)
        stream = dict(stream)
        stream["services"] = [{"day": "sunday", "morning": morning, "evening": evening,
                                "sunday_school_break": ssb}] if (morning or evening) else []
    return stream

def _validate_time_text(value, field):
    """Abort 400 unless value is a valid HH:MM time (or None)."""
    if value is None:
        return None
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError:
        abort(400, description=f"Invalid {field} time: {value!r}. Use 24h HH:MM format.")
    return value

def _parse_slots_from_form():
    try:
        count = int(request.form.get("slot_count", "0"))
    except ValueError:
        abort(400, description="slot_count must be a number.")
    slots = []
    for i in range(count):
        day = request.form.get(f"slot_day_{i}", "").strip().lower()
        if not day:
            continue
        if day not in DAYS_LIST:
            abort(400, description=f"Invalid day: {day!r}.")
        morning = _validate_time_text(request.form.get(f"slot_morning_{i}", "").strip() or None, "morning")
        evening = _validate_time_text(request.form.get(f"slot_evening_{i}", "").strip() or None, "evening")
        ssb = request.form.get(f"slot_ssb_{i}") == "on"
        slot = {"day": day}
        if morning:
            slot["morning"] = morning
        if evening:
            slot["evening"] = evening
        if ssb:
            slot["sunday_school_break"] = True
        slots.append(slot)
    return slots

print("Recordings directory contents:", os.listdir(RECORDINGS_FOLDER))
print("Transcriptions directory contents:", os.listdir(TRANSCRIPTIONS_FOLDER))

def get_audio_lengths(files):
    """
    Get lengths of audio files in seconds.
    
    :param files: List of audio file names.
    :return: Dictionary with file names as keys and lengths in seconds as values.
    """
    lengths = {}
    for file in files:
        file_path = os.path.join(RECORDINGS_FOLDER, file)
        if os.path.exists(file_path):
            try:
                audio = MP3(file_path)
                lengths[file] = f"{int(audio.info.length // 3600)}:{int((audio.info.length % 3600) // 60)}:{int(audio.info.length % 60)}"
            except Exception as e:
                print(f"Error reading audio file {file}: {e}")
                lengths[file] = "Unknown"
        else:
            lengths[file] = None
    return lengths

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username == USERNAME and password == PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        return "ERROR not found, login failed", 401
    return render_template("login.html")

@app.route("/index")
def index():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    files = [f for f in os.listdir(RECORDINGS_FOLDER) if f.lower().endswith(".mp3")]
    transcriptions = {}
    for f in files:
        txt_file = os.path.splitext(f)[0] + ".txt"
        txt_path = os.path.join(TRANSCRIPTIONS_FOLDER, txt_file)
        print(txt_path)
        if os.path.exists(txt_path):
            with open(txt_path, "r", encoding="utf-8") as t:
                transcriptions[f] = t.read()
        else:
            transcriptions[f] = "Transcription not available yet."
    return render_template("index.html", files=files, audio_lengths=get_audio_lengths(files), transcriptions=transcriptions)

@app.route("/recordings/<filename>")
def serve_recording(filename):
    if not session.get("logged_in"):
        abort(403)
    return send_from_directory(RECORDINGS_FOLDER, filename)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/appicon.png")
def serve_icon():
    icon_path = os.path.join(os.getcwd(), "appicon.png")
    if not os.path.exists(icon_path):
        abort(404)
    return send_from_directory(os.getcwd(), "appicon.png")

@app.route("/config")
def config_dashboard():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    data = load_streams()
    streams = [_normalize_stream(s) for s in data.get("streams", [])]
    return render_template("config.html", streams=streams, timezones=COMMON_TIMEZONES, days=DAYS_LIST)

@app.route("/config/stream/add", methods=["POST"])
def config_stream_add():
    _require_login()
    name = request.form.get("name", "").strip()
    if not name:
        abort(400)
    data = load_streams()
    streams = data.get("streams", [])
    new_stream = _build_stream_dict_from_form()
    new_stream["name"] = name
    streams.append(new_stream)
    data["streams"] = streams
    save_streams(data)
    return redirect(url_for("config_dashboard"))

@app.route("/config/stream/<int:idx>/edit", methods=["POST"])
def config_stream_edit(idx):
    _require_login()
    data, streams = _load_and_validate_stream_index(idx)
    streams[idx] = _build_stream_dict_from_form()
    data["streams"] = streams
    save_streams(data)
    return redirect(url_for("config_dashboard"))

@app.route("/config/stream/<int:idx>/toggle", methods=["POST"])
def config_stream_toggle(idx):
    _require_login()
    data, streams = _load_and_validate_stream_index(idx)
    streams[idx]["enabled"] = not streams[idx].get("enabled", True)
    data["streams"] = streams
    save_streams(data)
    return redirect(url_for("config_dashboard"))

@app.route("/config/stream/<int:idx>/delete", methods=["POST"])
def config_stream_delete(idx):
    _require_login()
    data, streams = _load_and_validate_stream_index(idx)
    streams.pop(idx)
    data["streams"] = streams
    save_streams(data)
    return redirect(url_for("config_dashboard"))

@app.route("/health")
def healths():
    return {"status": "ok"},200

if __name__ == "__main__":
    host = "0.0.0.0"
    port = 5003
    print(f"Web server starting on http://{host}:{port}")
    app.run(debug=os.getenv("FLASK_DEBUG") == "1", host=host, port=port)
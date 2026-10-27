from flask import Flask, render_template, request, redirect, url_for, send_from_directory, session, abort, flash
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import check_password_hash
import hmac
import ipaddress
import os
import secrets
import threading
import time
import yaml
import pytz
import mutagen
from datetime import datetime, timedelta
from mutagen.mp3 import MP3

app = Flask(__name__)

# Session signing key. Without a fixed key a random one is used, which logs
# everyone out whenever the container restarts.
_secret_key = os.getenv("FLASK_SECRET_KEY")
if not _secret_key:
    print("WARNING: FLASK_SECRET_KEY is not set — using a random key; admin sessions will not survive a restart.")
    _secret_key = secrets.token_hex(32)
app.secret_key = _secret_key
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Served over HTTPS through the Cloudflare tunnel; set SESSION_COOKIE_SECURE=0
    # to log in over plain http (e.g. directly on the LAN at :5003).
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "1") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=int(os.getenv("SESSION_LIFETIME_HOURS", "12"))),
)

# Enable CSRF protection
csrf = CSRFProtect(app)

# Admin credentials. The password is stored as a werkzeug hash — generate one
# with `python set_admin_password.py`. With no hash set, admin login is disabled.
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")
if not ADMIN_PASSWORD_HASH:
    print("WARNING: ADMIN_PASSWORD_HASH is not set — admin login is disabled.")

# Through the Cloudflare tunnel every request arrives from cloudflared, so the
# real visitor IP is read from CF-Connecting-IP — but only when the request
# actually comes from one of these addresses (comma-separated IPs or CIDRs).
# Anyone reaching :5003 directly (Tailscale, LAN) is identified by their own
# socket address, so they can't fake the header to dodge the login lockout.
def _parse_networks(value):
    nets = []
    for item in filter(None, (v.strip() for v in value.split(","))):
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            print(f"WARNING: ignoring invalid TRUSTED_PROXY_IPS entry {item!r}")
    return nets

TRUSTED_PROXY_NETWORKS = _parse_networks(os.getenv("TRUSTED_PROXY_IPS", ""))

# Brute-force protection: MAX_LOGIN_ATTEMPTS failures within LOGIN_WINDOW_SECONDS
# locks that IP out for LOGIN_WINDOW_SECONDS. Kept in memory, so it resets on restart.
MAX_LOGIN_ATTEMPTS = int(os.getenv("MAX_LOGIN_ATTEMPTS", "5"))
LOGIN_WINDOW_SECONDS = int(os.getenv("LOGIN_LOCKOUT_MINUTES", "15")) * 60
_login_failures = {}   # ip -> [failure timestamps]
_login_locked_until = {}  # ip -> unix time the lockout ends
_login_lock = threading.Lock()

# A real hash to check against when the username is wrong, so a bad username
# takes as long as a bad password and doesn't reveal which one was wrong.
_DUMMY_HASH = "scrypt:32768:8:1$dummysaltdummys$" + "0" * 128

RECORDINGS_FOLDER = os.getenv("RECORDINGS_FOLDER", "/app/recordings")
TRANSCRIPTIONS_FOLDER = os.getenv("TRANSCRIPTIONS_FOLDER", "/app/transcriptions")
CONFIG_FILE = os.getenv("CONFIG_FILE", "/app/config/streams.yml")

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

def get_audio_length(rel_path):
    """Return an audio file's length as H:MM:SS, "Unknown" if unreadable, or None if missing."""
    file_path = os.path.join(RECORDINGS_FOLDER, rel_path)
    if not os.path.exists(file_path):
        return None
    try:
        length = MP3(file_path).info.length
        return f"{int(length // 3600)}:{int((length % 3600) // 60):02d}:{int(length % 60):02d}"
    except Exception as e:
        print(f"Error reading audio file {rel_path}: {e}")
        return "Unknown"

def _read_transcription(rel_path):
    """Return the transcription for a recording, which mirrors its path under TRANSCRIPTIONS_FOLDER."""
    txt_path = os.path.join(TRANSCRIPTIONS_FOLDER, os.path.splitext(rel_path)[0] + ".txt")
    if os.path.exists(txt_path):
        with open(txt_path, "r", encoding="utf-8") as t:
            return t.read()
    return "Transcription not available yet."

def _stream_title(dir_name):
    """Turn a stream's output directory slug into a readable heading."""
    return dir_name.replace("_", " ").title() if dir_name else "Other Recordings"

def get_recording_groups():
    """
    Collect recordings grouped by stream directory.

    The recorder writes recordings/<stream_slug>/recording_<YYYY-MM-DD_HH-MM>.mp3,
    so a reverse filename sort puts the newest first. Loose .mp3 files at the
    top level (older layout) go in an "Other Recordings" group.
    Returns a list of {"title", "recordings": [{"path", "name", "length", "transcription"}]}
    sorted by title, skipping streams with no recordings.
    """
    by_dir = {}
    for entry in os.listdir(RECORDINGS_FOLDER):
        full = os.path.join(RECORDINGS_FOLDER, entry)
        if os.path.isdir(full):
            by_dir[entry] = [f"{entry}/{f}" for f in os.listdir(full) if f.lower().endswith(".mp3")]
        elif entry.lower().endswith(".mp3"):
            by_dir.setdefault("", []).append(entry)

    groups = []
    for dir_name, paths in by_dir.items():
        if not paths:
            continue
        paths.sort(key=os.path.basename, reverse=True)
        groups.append({
            "title": _stream_title(dir_name),
            "recordings": [{
                "path": p,
                "name": os.path.basename(p),
                "length": get_audio_length(p),
                "transcription": _read_transcription(p),
            } for p in paths],
        })
    groups.sort(key=lambda g: (g["title"] == "Other Recordings", g["title"].lower()))
    return groups

@app.route("/")
@app.route("/index")
def index():
    return render_template("index.html", groups=get_recording_groups(), logged_in=session.get("logged_in", False))

def _is_trusted_proxy(addr):
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in TRUSTED_PROXY_NETWORKS)

def _client_ip():
    remote = request.remote_addr or "unknown"
    if _is_trusted_proxy(remote):
        forwarded = request.headers.get("CF-Connecting-IP", "").strip()
        if forwarded:
            return forwarded
    return remote

def _lockout_remaining(ip):
    """Seconds left on this IP's lockout, or 0 if it may try to log in."""
    now = time.time()
    with _login_lock:
        until = _login_locked_until.get(ip, 0)
        if until > now:
            return int(until - now) + 1
        _login_locked_until.pop(ip, None)
        return 0

def _record_login_failure(ip):
    now = time.time()
    with _login_lock:
        recent = [t for t in _login_failures.get(ip, []) if now - t < LOGIN_WINDOW_SECONDS]
        recent.append(now)
        if len(recent) >= MAX_LOGIN_ATTEMPTS:
            _login_locked_until[ip] = now + LOGIN_WINDOW_SECONDS
            _login_failures.pop(ip, None)
            print(f"WARNING: locking out {ip} after {len(recent)} failed admin logins.")
        else:
            _login_failures[ip] = recent
        # Drop stale entries so the tables can't grow without bound.
        if len(_login_failures) > 1000:
            for k in [k for k, v in _login_failures.items() if now - v[-1] >= LOGIN_WINDOW_SECONDS]:
                del _login_failures[k]

def _check_credentials(username, password):
    user_ok = hmac.compare_digest(username.encode(), ADMIN_USERNAME.encode())
    try:
        pass_ok = check_password_hash(ADMIN_PASSWORD_HASH if user_ok else _DUMMY_HASH, password)
    except ValueError:
        print("ERROR: ADMIN_PASSWORD_HASH is not a valid werkzeug hash — regenerate it with set_admin_password.py.")
        return False
    return user_ok and pass_ok

def _minutes(seconds):
    return max(1, -(-seconds // 60))

@app.route("/login", methods=["GET", "POST"])
def login():
    if not ADMIN_PASSWORD_HASH:
        return render_template("login.html", disabled=True), 503
    if session.get("logged_in"):
        return redirect(url_for("config_dashboard"))
    if request.method == "GET":
        return render_template("login.html")

    ip = _client_ip()
    remaining = _lockout_remaining(ip)
    if remaining:
        flash(f"Too many failed attempts. Try again in {_minutes(remaining)} min.", "error")
        return render_template("login.html"), 429

    username = request.form.get("username", "")
    password = request.form.get("password", "")
    if _check_credentials(username, password):
        with _login_lock:
            _login_failures.pop(ip, None)
        # Fresh session on login so a pre-login session can't be reused.
        session.clear()
        session.permanent = True
        session["logged_in"] = True
        return redirect(url_for("config_dashboard"))

    _record_login_failure(ip)
    remaining = _lockout_remaining(ip)
    if remaining:
        flash(f"Too many failed attempts. Try again in {_minutes(remaining)} min.", "error")
        return render_template("login.html", username=username), 429
    flash("Invalid username or password.", "error")
    return render_template("login.html", username=username), 401

@app.route("/recordings/<path:filename>")
def serve_recording(filename):
    return send_from_directory(RECORDINGS_FOLDER, filename)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

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
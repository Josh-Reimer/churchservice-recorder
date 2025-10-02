from flask import Flask, render_template, request, redirect, url_for, send_from_directory, session, abort
from flask_wtf.csrf import CSRFProtect
import os
import mutagen
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

if __name__ == "__main__":
    host = "0.0.0.0"
    port = 5000
    app.run(debug=True, host="0.0.0.0", port=5000)
    print(f"Web server started on http://{host}:{port}")
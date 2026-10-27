"""Route tests against the real Flask app: public recordings page, login-gated config."""
import importlib
import os

import pytest
import yaml
from werkzeug.security import generate_password_hash

PASSWORD = "correct horse battery"


@pytest.fixture
def client(tmp_path, monkeypatch):
    rec = tmp_path / "recordings"
    txt = tmp_path / "transcriptions"
    cfg = tmp_path / "config"
    for d in (rec, txt, cfg):
        d.mkdir()
    (cfg / "streams.yml").write_text(yaml.dump({"streams": []}))
    monkeypatch.setenv("RECORDINGS_FOLDER", str(rec))
    monkeypatch.setenv("TRANSCRIPTIONS_FOLDER", str(txt))
    monkeypatch.setenv("CONFIG_FILE", str(cfg / "streams.yml"))
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", generate_password_hash(PASSWORD))
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "0")
    # The test client connects from 127.0.0.1; treat that as cloudflared.
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "127.0.0.1")

    import webserver
    webserver = importlib.reload(webserver)
    webserver.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with webserver.app.test_client() as c:
        c.rec, c.txt, c.ws = rec, txt, webserver
        yield c


def _add(client, stream, name, transcript=None):
    (client.rec / stream).mkdir(exist_ok=True)
    (client.rec / stream / name).write_bytes(b"not really mp3")
    if transcript is not None:
        (client.txt / stream).mkdir(exist_ok=True)
        (client.txt / stream / name.replace(".mp3", ".txt")).write_text(transcript)


def _login(client, password=PASSWORD, username="admin", ip=None, remote="127.0.0.1"):
    headers = {"CF-Connecting-IP": ip} if ip else {}
    return client.post("/login", data={"username": username, "password": password},
                       headers=headers, environ_base={"REMOTE_ADDR": remote})


def test_recordings_in_stream_subdirs_are_listed(client):
    _add(client, "bredenbury_congregation_bredenbury_sk", "recording_2026-10-25_10-00.mp3", "hello sermon")
    body = client.get("/").get_data(as_text=True)
    assert "Bredenbury Congregation Bredenbury Sk" in body
    assert "recording_2026-10-25_10-00.mp3" in body
    assert "hello sermon" in body
    assert "/recordings/bredenbury_congregation_bredenbury_sk/recording_2026-10-25_10-00.mp3" in body


def test_missing_transcription_shows_placeholder(client):
    _add(client, "s", "recording_2026-10-25_10-00.mp3")
    assert "Transcription not available yet." in client.get("/index").get_data(as_text=True)


def test_newest_first_within_group_and_groups_alphabetical(client):
    _add(client, "zeta_church", "recording_2026-10-25_10-00.mp3")
    _add(client, "alpha_church", "recording_2026-10-18_10-00.mp3")
    _add(client, "alpha_church", "recording_2026-10-25_18-00.mp3")
    _add(client, "alpha_church", "recording_2026-10-25_10-00.mp3")
    _add(client, "empty_church", "notes.txt")
    body = client.get("/").get_data(as_text=True)
    assert body.index("Alpha Church") < body.index("Zeta Church")
    assert "Empty Church" not in body
    alpha = body[body.index("Alpha Church"):body.index("Zeta Church")]
    order = [alpha.index(f"recording_2026-{d}.mp3</h3>") for d in ("10-25_18-00", "10-25_10-00", "10-18_10-00")]
    assert order == sorted(order)


def test_empty_state(client):
    assert "No recordings yet." in client.get("/").get_data(as_text=True)


def test_recordings_page_and_audio_are_public(client):
    _add(client, "s", "recording_2026-10-25_10-00.mp3")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Admin Login" in resp.get_data(as_text=True)
    assert "/config" not in resp.get_data(as_text=True)
    audio = client.get("/recordings/s/recording_2026-10-25_10-00.mp3")
    assert audio.status_code == 200
    assert audio.data == b"not really mp3"


def test_audio_route_blocks_path_traversal(client):
    assert client.get("/recordings/../config/streams.yml").status_code == 404


def test_config_requires_login(client):
    resp = client.get("/config")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/login")
    assert client.post("/config/stream/add", data={"name": "x"}).status_code == 403
    assert client.post("/config/stream/0/delete").status_code == 403


def test_login_grants_config_and_logout_revokes(client):
    resp = _login(client)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/config")
    assert client.get("/config").status_code == 200
    assert "Logout" in client.get("/").get_data(as_text=True)
    client.get("/logout")
    assert client.get("/config").status_code == 302


def test_bad_login_rejected(client):
    resp = _login(client, password="nope")
    assert resp.status_code == 401
    assert "Invalid username or password." in resp.get_data(as_text=True)
    assert client.get("/config").status_code == 302


def test_wrong_username_rejected_with_same_message(client):
    resp = _login(client, username="root")
    assert resp.status_code == 401
    assert "Invalid username or password." in resp.get_data(as_text=True)


def test_old_default_password_no_longer_works(client):
    assert _login(client, password="42").status_code == 401


def test_lockout_after_repeated_failures(client):
    for _ in range(client.ws.MAX_LOGIN_ATTEMPTS - 1):
        assert _login(client, password="nope", ip="203.0.113.5").status_code == 401
    resp = _login(client, password="nope", ip="203.0.113.5")
    assert resp.status_code == 429
    assert "Too many failed attempts" in resp.get_data(as_text=True)
    # Even the right password is refused while locked out...
    assert _login(client, ip="203.0.113.5").status_code == 429
    assert client.get("/config").status_code == 302
    # ...but other clients behind the same tunnel are unaffected.
    assert _login(client, ip="198.51.100.7").status_code == 302


def test_lockout_expires(client, monkeypatch):
    for _ in range(client.ws.MAX_LOGIN_ATTEMPTS):
        _login(client, password="nope", ip="203.0.113.5")
    real_time = client.ws.time.time
    monkeypatch.setattr(client.ws.time, "time", lambda: real_time() + client.ws.LOGIN_WINDOW_SECONDS + 1)
    assert _login(client, ip="203.0.113.5").status_code == 302


def test_success_resets_failure_count(client):
    for _ in range(client.ws.MAX_LOGIN_ATTEMPTS - 1):
        _login(client, password="nope", ip="203.0.113.5")
    assert _login(client, ip="203.0.113.5").status_code == 302
    client.get("/logout")
    for _ in range(client.ws.MAX_LOGIN_ATTEMPTS - 1):
        assert _login(client, password="nope", ip="203.0.113.5").status_code == 401


def test_login_disabled_without_password_hash(client, monkeypatch):
    monkeypatch.setattr(client.ws, "ADMIN_PASSWORD_HASH", "")
    resp = client.get("/login")
    assert resp.status_code == 503
    assert "isn't configured" in resp.get_data(as_text=True)
    assert _login(client).status_code == 503
    assert client.get("/config").status_code == 302


def test_session_cookie_flags_and_lifetime(client):
    resp = _login(client)
    cookie = resp.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Expires=" in cookie


def test_secure_cookies_on_by_default(client, monkeypatch):
    monkeypatch.delenv("SESSION_COOKIE_SECURE")
    ws = importlib.reload(client.ws)
    assert ws.app.config["SESSION_COOKIE_SECURE"] is True


def test_spoofed_header_from_untrusted_source_is_ignored(client):
    # A Tailscale/LAN client sending a fresh fake CF-Connecting-IP every time
    # is still counted by its real address and gets locked out.
    for i in range(client.ws.MAX_LOGIN_ATTEMPTS - 1):
        assert _login(client, password="nope", ip=f"10.0.0.{i}", remote="100.101.102.103").status_code == 401
    assert _login(client, password="nope", ip="10.0.0.99", remote="100.101.102.103").status_code == 429
    assert _login(client, ip="10.0.0.100", remote="100.101.102.103").status_code == 429


def test_untrusted_client_cannot_lock_out_someone_else(client):
    for _ in range(client.ws.MAX_LOGIN_ATTEMPTS):
        _login(client, password="nope", ip="203.0.113.5", remote="100.101.102.103")
    # The real 203.0.113.5 coming through cloudflared is unaffected.
    assert _login(client, ip="203.0.113.5").status_code == 302


def test_x_forwarded_for_is_not_trusted(client):
    for i in range(client.ws.MAX_LOGIN_ATTEMPTS):
        client.post("/login", data={"username": "admin", "password": "nope"},
                    headers={"X-Forwarded-For": f"10.1.1.{i}"},
                    environ_base={"REMOTE_ADDR": "100.101.102.103"})
    assert _login(client, remote="100.101.102.103").status_code == 429


def test_trusted_proxy_list_parsing(client):
    nets = client.ws._parse_networks("192.168.0.20, 100.78.23.97,10.0.0.0/8,,not-an-ip")
    assert [str(n) for n in nets] == ["192.168.0.20/32", "100.78.23.97/32", "10.0.0.0/8"]

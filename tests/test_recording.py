"""Tests for the recording session logic: rejoining after short drops,
re-arming after long ones, ffmpeg restarts, the duration cap, one job per
stream, and Telegram upload limits."""
import os
import shutil
import subprocess
import threading
import time

import pytest

import new_recorder

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is needed to join parts")


@pytest.fixture(scope="module")
def tone_mp3(tmp_path_factory):
    path = tmp_path_factory.mktemp("tone") / "tone.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=2",
         "-c:a", "libmp3lame", "-b:a", "64k", str(path)],
        check=True,
    )
    return str(path)


class FakeProcess:
    """Stands in for an ffmpeg Popen: runs until terminated or `exit_after` seconds."""

    def __init__(self, exit_after=None):
        self.pid = 4242
        self._exit_at = time.monotonic() + exit_after if exit_after is not None else None
        self._terminated = False

    def poll(self):
        if self._terminated or (self._exit_at is not None and time.monotonic() >= self._exit_at):
            return 0
        return None

    def terminate(self):
        self._terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self._terminated = True


class Harness:
    """Scripted status API (online windows in seconds from start) plus fake ffmpeg."""

    def __init__(self, monkeypatch, tone_mp3, online_windows, ffmpeg_exits=()):
        self.start = time.monotonic()
        self.online_windows = online_windows
        self.ffmpeg_exits = list(ffmpeg_exits)  # per-part exit_after, None = run until stopped
        self.parts_started = []
        self.messages = []
        self.files_sent = []
        self.queued = []

        monkeypatch.setattr(new_recorder, "stream_available", self.stream_available)
        monkeypatch.setattr(new_recorder, "run_ffmpeg", self.run_ffmpeg)
        monkeypatch.setattr(new_recorder, "notify_telegram", self.messages.append)
        monkeypatch.setattr(new_recorder, "notify_telegram_file", lambda f, caption="": self.files_sent.append(f))
        monkeypatch.setattr(new_recorder, "queue_transcription", lambda f, d: self.queued.append(f))
        self.tone_mp3 = tone_mp3

    def elapsed(self):
        return time.monotonic() - self.start

    def stream_available(self, status_url):
        t = self.elapsed()
        return any(a <= t < b for a, b in self.online_windows)

    def run_ffmpeg(self, name, url, output_path, output_format="mp3"):
        shutil.copy(self.tone_mp3, f"{output_path}.{output_format}")
        self.parts_started.append(f"{output_path}.{output_format}")
        exit_after = self.ffmpeg_exits.pop(0) if self.ffmpeg_exits else None
        return FakeProcess(exit_after)


@pytest.fixture
def fast_timing(monkeypatch):
    monkeypatch.setattr(new_recorder, "CHECK_INTERVAL", 0.05)
    monkeypatch.setattr(new_recorder, "MERGE_GAP_MINUTES", 0.5 / 60)
    monkeypatch.setattr(new_recorder, "REARM_MINUTES", 1 / 60)
    monkeypatch.setattr(new_recorder, "MAX_RECORDING_MINUTES", 10 / 60)


def recordings(output_dir):
    return sorted(f for f in os.listdir(output_dir) if f.endswith(".mp3"))


def run_job(output_dir, has_sunday_school=False):
    new_recorder.record_stream("sunday_morning", "http://stream", "http://status",
                               str(output_dir), str(output_dir), has_sunday_school)


class TestRejoinAfterShortDrop:
    def test_short_drop_joins_into_one_file(self, monkeypatch, tone_mp3, fast_timing, tmp_path):
        h = Harness(monkeypatch, tone_mp3, online_windows=[(0, 1.0), (1.3, 2.0)])
        run_job(tmp_path)
        assert len(h.parts_started) == 2
        files = recordings(tmp_path)
        assert len(files) == 1, files
        assert ".part" not in files[0]
        # Both 2 s tones are in the joined file.
        assert os.path.getsize(tmp_path / files[0]) > 1.8 * os.path.getsize(tone_mp3)
        assert h.queued == [str(tmp_path / files[0])]

    def test_long_drop_starts_a_new_file(self, monkeypatch, tone_mp3, fast_timing, tmp_path):
        # recording_timestamp() has minute resolution, so give each file its own name.
        stamps = iter(["2026-10-25_10-00", "2026-10-25_10-45"])
        monkeypatch.setattr(new_recorder, "recording_timestamp", lambda: next(stamps))
        h = Harness(monkeypatch, tone_mp3, online_windows=[(0, 0.5), (1.5, 2.0)])
        run_job(tmp_path)
        assert recordings(tmp_path) == ["recording_2026-10-25_10-00.mp3", "recording_2026-10-25_10-45.mp3"]
        assert len(h.queued) == 2

    def test_job_ends_when_stream_never_returns(self, monkeypatch, tone_mp3, fast_timing, tmp_path):
        h = Harness(monkeypatch, tone_mp3, online_windows=[(0, 0.5)])
        t0 = time.monotonic()
        run_job(tmp_path)
        assert len(recordings(tmp_path)) == 1
        # Merge gap (0.5 s) + re-arm window (1 s), not the 90 min initial timeout.
        assert time.monotonic() - t0 < 4
        assert not any("did not become available" in m for m in h.messages)


class TestFfmpegRestart:
    def test_ffmpeg_dying_while_online_is_restarted_into_same_file(self, monkeypatch, tone_mp3, fast_timing, tmp_path):
        h = Harness(monkeypatch, tone_mp3, online_windows=[(0, 2.0)], ffmpeg_exits=[0.5])
        run_job(tmp_path)
        assert len(h.parts_started) >= 2
        assert len(recordings(tmp_path)) == 1


class TestDurationCap:
    def test_stream_left_on_is_capped(self, monkeypatch, tone_mp3, fast_timing, tmp_path):
        monkeypatch.setattr(new_recorder, "MAX_RECORDING_MINUTES", 1 / 60)
        h = Harness(monkeypatch, tone_mp3, online_windows=[(0, 999)])
        t0 = time.monotonic()
        run_job(tmp_path)
        assert time.monotonic() - t0 < 3
        assert len(recordings(tmp_path)) == 1
        assert any("Recording stopped after" in m for m in h.messages)


class TestOneJobPerStream:
    def test_newer_trigger_takes_over_from_running_job(self, monkeypatch, tone_mp3, fast_timing, tmp_path):
        stamps = iter(["2026-10-25_10-00", "2026-10-25_18-00"])
        monkeypatch.setattr(new_recorder, "recording_timestamp", lambda: next(stamps))
        monkeypatch.setattr(new_recorder, "MAX_RECORDING_MINUTES", 3 / 60)
        h = Harness(monkeypatch, tone_mp3, online_windows=[(0, 999)])
        first = threading.Thread(target=run_job, args=(tmp_path,))
        first.start()
        time.sleep(0.5)
        second = threading.Thread(target=run_job, args=(tmp_path,))
        second.start()
        first.join(timeout=2)
        assert not first.is_alive(), "older job should finish when a newer one starts"
        second.join(timeout=5)
        assert recordings(tmp_path) == ["recording_2026-10-25_10-00.mp3", "recording_2026-10-25_18-00.mp3"]


class TestTelegram:
    def test_large_file_is_not_uploaded(self, monkeypatch, tmp_path):
        big = tmp_path / "big.mp3"
        with open(big, "wb") as f:
            f.truncate(new_recorder.TELEGRAM_MAX_UPLOAD_BYTES + 1)
        sent, messages = [], []
        monkeypatch.setattr(new_recorder, "TELEGRAM_ENABLED", True)
        monkeypatch.setattr(new_recorder, "send_telegram_file", lambda *a, **k: sent.append(a))
        monkeypatch.setattr(new_recorder, "notify_telegram", messages.append)
        new_recorder.notify_telegram_file(str(big), caption="Recording finished: big.mp3")
        assert sent == []
        assert "too large" in messages[0]

    def test_token_is_redacted(self, monkeypatch):
        monkeypatch.setattr(new_recorder, "TELEGRAM_BOT_TOKEN", "123:SECRET")
        assert "SECRET" not in new_recorder._redact("413 for url: https://api.telegram.org/bot123:SECRET/sendDocument")

"""Tests for StreamInfo and ServiceSlot data models."""
import pytest
import pytz
from datetime import datetime
from new_recorder import StreamInfo, ServiceSlot


class TestServiceSlot:
    """Test cases for the ServiceSlot class."""

    def test_service_slot_initialization(self):
        """Test basic ServiceSlot creation."""
        morning_time = datetime.now(pytz.UTC)
        evening_time = datetime.now(pytz.UTC)

        slot = ServiceSlot(
            day_of_week=6,  # Sunday
            morning_time_text="10:00",
            evening_time_text="18:00",
            morning_time=morning_time,
            evening_time=evening_time,
            sunday_school_break=True,
        )

        assert slot.day_of_week == 6
        assert slot.morning_time_text == "10:00"
        assert slot.evening_time_text == "18:00"
        assert slot.morning_time == morning_time
        assert slot.evening_time == evening_time
        assert slot.sunday_school_break is True
        assert slot.last_fired == {}

    def test_service_slot_last_fired_tracks_by_time_of_day(self):
        """Test that last_fired tracks morning and evening separately."""
        slot = ServiceSlot(
            day_of_week=6,
            morning_time_text="10:00",
            evening_time_text="18:00",
            morning_time=datetime.now(pytz.UTC),
            evening_time=datetime.now(pytz.UTC),
        )

        # Fire morning
        slot.last_fired["morning"] = "2024-06-16"
        assert slot.last_fired["morning"] == "2024-06-16"
        assert "evening" not in slot.last_fired

        # Fire evening
        slot.last_fired["evening"] = "2024-06-16"
        assert slot.last_fired["evening"] == "2024-06-16"

    def test_service_slot_without_sunday_school_break(self):
        """Test ServiceSlot with sunday_school_break disabled."""
        slot = ServiceSlot(
            day_of_week=0,  # Monday
            morning_time_text=None,
            evening_time_text="19:00",
            morning_time=None,
            evening_time=datetime.now(pytz.UTC),
            sunday_school_break=False,
        )

        assert slot.sunday_school_break is False
        assert slot.morning_time is None


class TestStreamInfo:
    """Test cases for the StreamInfo class."""

    def test_stream_info_initialization(self, chicago_tz):
        """Test basic StreamInfo creation."""
        slots = [
            ServiceSlot(
                day_of_week=6,
                morning_time_text="10:00",
                evening_time_text="18:00",
                morning_time=datetime.now(chicago_tz),
                evening_time=datetime.now(chicago_tz),
            )
        ]

        stream = StreamInfo(
            name="main",
            url="https://example.com/stream.mp3",
            status_url="https://example.com/status",
            timezone="America/Chicago",
            stream_tz=chicago_tz,
            audio_dir="/recordings/main",
            transcription_dir="/transcriptions/main",
            slots=slots,
            enabled=True,
        )

        assert stream.name == "main"
        assert stream.url == "https://example.com/stream.mp3"
        assert stream.status_url == "https://example.com/status"
        assert stream.timezone == "America/Chicago"
        assert stream.stream_tz == chicago_tz
        assert stream.audio_dir == "/recordings/main"
        assert stream.transcription_dir == "/transcriptions/main"
        assert len(stream.slots) == 1
        assert stream.enabled is True

    def test_stream_info_disabled(self, chicago_tz):
        """Test creating a disabled stream."""
        stream = StreamInfo(
            name="disabled",
            url="https://example.com/stream.mp3",
            status_url="https://example.com/status",
            timezone="America/Chicago",
            stream_tz=chicago_tz,
            audio_dir="/recordings/disabled",
            transcription_dir="/transcriptions/disabled",
            slots=[],
            enabled=False,
        )

        assert stream.enabled is False

    def test_stream_info_multiple_slots(self, chicago_tz):
        """Test StreamInfo with multiple service slots."""
        slots = [
            ServiceSlot(
                day_of_week=6,
                morning_time_text="10:00",
                evening_time_text="18:00",
                morning_time=datetime.now(chicago_tz),
                evening_time=datetime.now(chicago_tz),
            ),
            ServiceSlot(
                day_of_week=2,  # Wednesday
                morning_time_text=None,
                evening_time_text="19:00",
                morning_time=None,
                evening_time=datetime.now(chicago_tz),
            ),
        ]

        stream = StreamInfo(
            name="multi",
            url="https://example.com/stream.mp3",
            status_url="https://example.com/status",
            timezone="America/Chicago",
            stream_tz=chicago_tz,
            audio_dir="/recordings/multi",
            transcription_dir="/transcriptions/multi",
            slots=slots,
            enabled=True,
        )

        assert len(stream.slots) == 2
        assert stream.slots[0].day_of_week == 6
        assert stream.slots[1].day_of_week == 2

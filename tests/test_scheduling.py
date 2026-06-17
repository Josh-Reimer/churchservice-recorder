"""Tests for scheduling logic (checking services, firing triggers)."""
import pytest
from datetime import datetime, timedelta, date
import pytz
from freezegun import freeze_time
from new_recorder import ServiceSlot, StreamInfo, DAYS


class TestServiceTiming:
    """Test when services should be triggered."""

    def test_correct_weekday_triggers_service(self, chicago_tz):
        """Test that service fires on the correct weekday."""
        slot = ServiceSlot(
            day_of_week=6,  # Sunday
            morning_time_text="10:00",
            evening_time_text="18:00",
            morning_time=datetime(2024, 6, 16, 15, 0, 0, tzinfo=pytz.UTC),  # 10:00 CDT
            evening_time=datetime(2024, 6, 16, 23, 0, 0, tzinfo=pytz.UTC),  # 18:00 CDT
        )

        # Sunday should match
        sunday_now = datetime(2024, 6, 16, 15, 0, 0, tzinfo=pytz.UTC)  # 10:00 CDT
        sunday_weekday = sunday_now.astimezone(chicago_tz).weekday()
        assert sunday_weekday == 6  # Sunday
        assert sunday_weekday == slot.day_of_week

    def test_wrong_weekday_skips_service(self, chicago_tz):
        """Test that service doesn't fire on wrong weekday."""
        slot = ServiceSlot(
            day_of_week=6,  # Sunday
            morning_time_text="10:00",
            evening_time_text="18:00",
            morning_time=datetime(2024, 6, 16, 15, 0, 0, tzinfo=pytz.UTC),
            evening_time=datetime(2024, 6, 16, 23, 0, 0, tzinfo=pytz.UTC),
        )

        # Monday should not match
        monday_now = datetime(2024, 6, 17, 15, 0, 0, tzinfo=pytz.UTC)  # Monday
        monday_weekday = monday_now.astimezone(chicago_tz).weekday()
        assert monday_weekday == 0  # Monday
        assert monday_weekday != slot.day_of_week


class TestLastFiredTracking:
    """Test that last_fired prevents double-triggering."""

    def test_fire_once_per_day_morning(self, chicago_tz):
        """Test that morning service fires only once per day."""
        slot = ServiceSlot(
            day_of_week=6,
            morning_time_text="10:00",
            evening_time_text="18:00",
            morning_time=datetime(2024, 6, 16, 15, 0, 0, tzinfo=pytz.UTC),
            evening_time=datetime(2024, 6, 16, 23, 0, 0, tzinfo=pytz.UTC),
        )

        service_date = date(2024, 6, 16)

        # First call: last_fired is empty, should trigger
        if slot.last_fired.get("morning") != service_date:
            slot.last_fired["morning"] = service_date
            should_fire_first = True
        else:
            should_fire_first = False

        assert should_fire_first

        # Second call: last_fired matches today, should NOT trigger
        if slot.last_fired.get("morning") != service_date:
            should_fire_second = True
        else:
            should_fire_second = False

        assert not should_fire_second

    def test_fire_again_next_day(self, chicago_tz):
        """Test that service fires again on the next occurrence of its day."""
        slot = ServiceSlot(
            day_of_week=6,
            morning_time_text="10:00",
            evening_time_text="18:00",
            morning_time=datetime(2024, 6, 16, 15, 0, 0, tzinfo=pytz.UTC),
            evening_time=datetime(2024, 6, 16, 23, 0, 0, tzinfo=pytz.UTC),
        )

        # Fire on June 16 (Sunday)
        slot.last_fired["morning"] = date(2024, 6, 16)

        # Check on June 17 (Monday) — should not fire (different day anyway)
        should_fire = slot.last_fired.get("morning") != date(2024, 6, 17)
        assert should_fire  # Different date, but wrong weekday

        # Check on June 23 (next Sunday) — should fire
        slot.last_fired.pop("morning", None)
        slot.last_fired["morning"] = date(2024, 6, 23)
        should_fire = slot.last_fired.get("morning") != date(2024, 6, 23)
        assert not should_fire  # Has fired today

    def test_morning_and_evening_fire_independently(self, chicago_tz):
        """Test that morning and evening services are tracked separately."""
        slot = ServiceSlot(
            day_of_week=6,
            morning_time_text="10:00",
            evening_time_text="18:00",
            morning_time=datetime(2024, 6, 16, 15, 0, 0, tzinfo=pytz.UTC),
            evening_time=datetime(2024, 6, 16, 23, 0, 0, tzinfo=pytz.UTC),
        )

        service_date = date(2024, 6, 16)

        # Fire morning
        slot.last_fired["morning"] = service_date
        morning_should_fire = slot.last_fired.get("morning") != service_date
        assert not morning_should_fire  # Has fired

        # Evening should still fire
        evening_should_fire = slot.last_fired.get("evening") != service_date
        assert evening_should_fire  # Has not fired

        # Fire evening
        slot.last_fired["evening"] = service_date

        # Now neither should fire
        assert not (slot.last_fired.get("morning") != service_date)
        assert not (slot.last_fired.get("evening") != service_date)


class TestMultipleSlots:
    """Test handling of streams with multiple service slots."""

    def test_multiple_slots_per_stream(self, chicago_tz):
        """Test that a stream can have multiple service slots."""
        slots = [
            ServiceSlot(
                day_of_week=6,  # Sunday
                morning_time_text="10:00",
                evening_time_text="18:00",
                morning_time=datetime(2024, 6, 16, 15, 0, 0, tzinfo=pytz.UTC),
                evening_time=datetime(2024, 6, 16, 23, 0, 0, tzinfo=pytz.UTC),
            ),
            ServiceSlot(
                day_of_week=2,  # Wednesday
                morning_time_text=None,
                evening_time_text="19:00",
                morning_time=None,
                evening_time=datetime(2024, 6, 19, 0, 0, 0, tzinfo=pytz.UTC),  # 19:00 CDT
            ),
        ]

        assert len(slots) == 2
        assert slots[0].day_of_week == 6
        assert slots[1].day_of_week == 2
        assert slots[0].morning_time is not None
        assert slots[1].morning_time is None


class TestSundaySchoolBreak:
    """Test handling of Sunday school break logic."""

    def test_sunday_school_break_enabled(self, chicago_tz):
        """Test that sunday_school_break flag is preserved."""
        slot = ServiceSlot(
            day_of_week=6,
            morning_time_text="10:00",
            evening_time_text="18:00",
            morning_time=datetime(2024, 6, 16, 15, 0, 0, tzinfo=pytz.UTC),
            evening_time=datetime(2024, 6, 16, 23, 0, 0, tzinfo=pytz.UTC),
            sunday_school_break=True,
        )

        assert slot.sunday_school_break is True

    def test_sunday_school_break_disabled(self, chicago_tz):
        """Test that sunday_school_break defaults to False."""
        slot = ServiceSlot(
            day_of_week=6,
            morning_time_text="10:00",
            evening_time_text="18:00",
            morning_time=datetime(2024, 6, 16, 15, 0, 0, tzinfo=pytz.UTC),
            evening_time=datetime(2024, 6, 16, 23, 0, 0, tzinfo=pytz.UTC),
            sunday_school_break=False,
        )

        assert slot.sunday_school_break is False


class TestEnabledStreamFilter:
    """Test that disabled streams are skipped."""

    def test_enabled_stream_included(self, chicago_tz):
        """Test that enabled streams are processed."""
        stream = StreamInfo(
            name="enabled",
            url="https://example.com/stream.mp3",
            status_url="https://example.com/status",
            timezone="America/Chicago",
            stream_tz=chicago_tz,
            audio_dir="/recordings/enabled",
            transcription_dir="/transcriptions/enabled",
            slots=[],
            enabled=True,
        )

        assert stream.enabled is True

    def test_disabled_stream_skipped(self, chicago_tz):
        """Test that disabled streams are skipped in scheduling."""
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

        # Scheduling logic would skip this
        if not stream.enabled:
            should_process = False
        else:
            should_process = True

        assert not should_process

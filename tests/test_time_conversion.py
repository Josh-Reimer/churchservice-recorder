"""Tests for timezone and time conversion logic."""
import pytest
import pytz
from datetime import datetime, timedelta
from freezegun import freeze_time


def convert_time(t, stream_tz, service_date=None):
    """
    Convert a time string (HH:MM) to a tz-aware datetime in the system's local tz.
    This is a copy of the function from new_recorder.py for testing.
    """
    local_tz = pytz.timezone("UTC")  # Default for tests

    if not t or str(t).strip().upper() == "N/A":
        return None
    dt = datetime.strptime(str(t), "%H:%M")
    service_date = service_date or datetime.now(stream_tz).date()
    naive_service_time = datetime.combine(service_date, dt.time())
    try:
        localized = stream_tz.localize(naive_service_time, is_dst=None)
    except pytz.NonExistentTimeError:
        localized = stream_tz.localize(naive_service_time + timedelta(hours=1), is_dst=True)
    except pytz.AmbiguousTimeError:
        localized = stream_tz.localize(naive_service_time, is_dst=False)
    return localized.astimezone(local_tz)


class TestTimeConversion:
    """Test time conversion between timezones."""

    def test_convert_time_basic(self, chicago_tz):
        """Test basic time conversion."""
        service_date = datetime(2024, 6, 16).date()  # Sunday
        result = convert_time("10:00", chicago_tz, service_date)

        assert result is not None
        assert result.hour == 15  # 10:00 CDT is 15:00 UTC (assuming summer)
        assert result.minute == 0

    def test_convert_time_evening(self, chicago_tz):
        """Test converting an evening time."""
        service_date = datetime(2024, 6, 16).date()  # Sunday
        result = convert_time("18:00", chicago_tz, service_date)

        assert result is not None
        assert result.hour == 23  # 18:00 CDT is 23:00 UTC

    def test_convert_time_with_explicit_date(self, chicago_tz):
        """Test that explicit date is used instead of today."""
        service_date = datetime(2024, 12, 25).date()  # Winter date
        result = convert_time("10:00", chicago_tz, service_date)

        assert result is not None
        # In winter, Chicago is CST (UTC-6), so 10:00 CST = 16:00 UTC
        assert result.hour == 16

    def test_convert_time_none_value(self, chicago_tz):
        """Test that None time returns None."""
        result = convert_time(None, chicago_tz)
        assert result is None

    def test_convert_time_na_string(self, chicago_tz):
        """Test that 'N/A' string returns None."""
        result = convert_time("N/A", chicago_tz)
        assert result is None

    def test_convert_time_various_timezones(self):
        """Test time conversion across different timezones."""
        service_date = datetime(2024, 6, 16).date()

        # Test New York (EDT, UTC-4)
        ny_tz = pytz.timezone("America/New_York")
        result_ny = convert_time("10:00", ny_tz, service_date)
        assert result_ny is not None
        assert result_ny.hour == 14  # 10:00 EDT is 14:00 UTC

        # Test Denver (MDT, UTC-6)
        denver_tz = pytz.timezone("America/Denver")
        result_denver = convert_time("10:00", denver_tz, service_date)
        assert result_denver is not None
        assert result_denver.hour == 16  # 10:00 MDT is 16:00 UTC

    def test_convert_time_midnight(self, chicago_tz):
        """Test converting midnight."""
        service_date = datetime(2024, 6, 16).date()
        result = convert_time("00:00", chicago_tz, service_date)

        assert result is not None
        assert result.minute == 0
        # Just verify it converted without error

    def test_convert_time_afternoon_edge_cases(self, chicago_tz):
        """Test times that might have edge cases."""
        service_date = datetime(2024, 6, 16).date()

        times = ["12:00", "12:30", "23:59"]
        for time_str in times:
            result = convert_time(time_str, chicago_tz, service_date)
            assert result is not None


class TestDaylightSavingTime:
    """Test handling of DST transitions."""

    def test_spring_forward_chicago(self, chicago_tz):
        """Test spring forward (2:00 AM becomes 3:00 AM)."""
        # March 10, 2024 is when DST starts in US
        spring_forward_date = datetime(2024, 3, 10).date()

        # A time that exists: 1:00 AM CST
        result = convert_time("01:00", chicago_tz, spring_forward_date)
        assert result is not None

    def test_fall_back_chicago(self, chicago_tz):
        """Test fall back (2:00 AM becomes 1:00 AM)."""
        # November 3, 2024 is when DST ends in US
        fall_back_date = datetime(2024, 11, 3).date()

        # A time that might be ambiguous during fall back: 1:30 AM
        result = convert_time("01:30", chicago_tz, fall_back_date)
        assert result is not None


class TestTimeComparison:
    """Test logic for comparing times in scheduling."""

    def test_current_time_within_trigger_window(self, chicago_tz):
        """Test checking if current time is within trigger window."""
        scheduled = datetime(2024, 6, 16, 15, 0, 0, tzinfo=pytz.UTC)  # 10:00 CDT
        current = datetime(2024, 6, 16, 15, 0, 30, tzinfo=pytz.UTC)  # 30 seconds later
        window = timedelta(seconds=60)

        # Should be within window
        assert scheduled <= current < scheduled + window

    def test_current_time_outside_trigger_window(self, chicago_tz):
        """Test checking if current time is outside trigger window."""
        scheduled = datetime(2024, 6, 16, 15, 0, 0, tzinfo=pytz.UTC)
        current = datetime(2024, 6, 16, 16, 5, 0, tzinfo=pytz.UTC)  # After window
        window = timedelta(seconds=60)

        # Should be outside window
        assert not (scheduled <= current < scheduled + window)

    def test_trigger_window_boundary(self):
        """Test exact boundary of trigger window."""
        scheduled = datetime(2024, 6, 16, 15, 0, 0, tzinfo=pytz.UTC)
        window = timedelta(seconds=60)

        # Just at the start: should trigger
        at_start = datetime(2024, 6, 16, 15, 0, 0, tzinfo=pytz.UTC)
        assert scheduled <= at_start < scheduled + window

        # Just before the start: should not trigger
        before_start = datetime(2024, 6, 16, 14, 59, 59, tzinfo=pytz.UTC)
        assert not (scheduled <= before_start < scheduled + window)

        # Just at the end (exclusive): should not trigger
        at_end = datetime(2024, 6, 16, 15, 1, 0, tzinfo=pytz.UTC)
        assert not (scheduled <= at_end < scheduled + window)

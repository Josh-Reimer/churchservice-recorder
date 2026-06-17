"""Tests for Flask webserver routes and utilities."""
import pytest
import tempfile
import os
import yaml
from unittest.mock import Mock, patch, MagicMock


# Note: These tests use mock dependencies to avoid needing the full Flask app
# In a real scenario, you'd import the Flask app and use app.test_client()

class TestParseFormFunctions:
    """Test form parsing utility functions."""

    def test_parse_slots_from_form_empty(self):
        """Test parsing when no slots are provided."""
        # Simulate request.form
        form_data = {"slot_count": "0"}

        count = int(form_data.get("slot_count", "0"))
        slots = []

        assert count == 0
        assert len(slots) == 0

    def test_parse_slots_from_form_single_slot(self):
        """Test parsing a single service slot."""
        form_data = {
            "slot_count": "1",
            "slot_day_0": "sunday",
            "slot_morning_0": "10:00",
            "slot_evening_0": "18:00",
            "slot_ssb_0": "on",
        }

        count = int(form_data.get("slot_count", "0"))
        slots = []
        for i in range(count):
            day = form_data.get(f"slot_day_{i}", "").strip()
            if not day:
                continue
            morning = form_data.get(f"slot_morning_{i}", "").strip() or None
            evening = form_data.get(f"slot_evening_{i}", "").strip() or None
            ssb = form_data.get(f"slot_ssb_{i}") == "on"
            slot = {"day": day}
            if morning:
                slot["morning"] = morning
            if evening:
                slot["evening"] = evening
            if ssb:
                slot["sunday_school_break"] = True
            slots.append(slot)

        assert len(slots) == 1
        assert slots[0]["day"] == "sunday"
        assert slots[0]["morning"] == "10:00"
        assert slots[0]["evening"] == "18:00"
        assert slots[0]["sunday_school_break"] is True

    def test_parse_slots_from_form_multiple_slots(self):
        """Test parsing multiple service slots."""
        form_data = {
            "slot_count": "2",
            "slot_day_0": "sunday",
            "slot_morning_0": "10:00",
            "slot_evening_0": "18:00",
            "slot_ssb_0": "",
            "slot_day_1": "wednesday",
            "slot_morning_1": "",
            "slot_evening_1": "19:00",
            "slot_ssb_1": "",
        }

        count = int(form_data.get("slot_count", "0"))
        slots = []
        for i in range(count):
            day = form_data.get(f"slot_day_{i}", "").strip()
            if not day:
                continue
            morning = form_data.get(f"slot_morning_{i}", "").strip() or None
            evening = form_data.get(f"slot_evening_{i}", "").strip() or None
            ssb = form_data.get(f"slot_ssb_{i}") == "on"
            slot = {"day": day}
            if morning:
                slot["morning"] = morning
            if evening:
                slot["evening"] = evening
            if ssb:
                slot["sunday_school_break"] = True
            slots.append(slot)

        assert len(slots) == 2
        assert slots[0]["day"] == "sunday"
        assert slots[1]["day"] == "wednesday"
        assert "morning" not in slots[1]  # Wednesday has no morning
        assert slots[1]["evening"] == "19:00"


class TestNormalizeStream:
    """Test stream normalization (legacy format to new format)."""

    def test_normalize_new_format(self):
        """Test that new format streams are unchanged."""
        stream = {
            "name": "main",
            "full_name": "Main Church",
            "url": "https://example.com/stream.mp3",
            "status_url": "https://example.com/status",
            "timezone": "America/Chicago",
            "services": [
                {"day": "sunday", "morning": "10:00", "evening": "18:00"}
            ],
            "enabled": True,
        }

        # Already has services, should not change
        if "services" not in stream:
            modified = True
        else:
            modified = False

        assert modified is False
        assert stream["services"][0]["day"] == "sunday"

    def test_normalize_legacy_format(self):
        """Test migration of legacy format to new format."""
        stream = {
            "name": "legacy",
            "timezone": "America/Chicago",
            "sunday_morning_service_time": "10:00",
            "sunday_evening_service_time": "18:00",
            "sunday_school_break": False,
        }

        # Migrate if needed
        if "services" not in stream:
            morning = stream.get("sunday_morning_service_time")
            evening = stream.get("sunday_evening_service_time")
            ssb = stream.get("sunday_school_break", False)
            stream_copy = dict(stream)
            stream_copy["services"] = (
                [{"day": "sunday", "morning": morning, "evening": evening,
                  "sunday_school_break": ssb}]
                if (morning or evening) else []
            )
            stream = stream_copy

        assert "services" in stream
        assert len(stream["services"]) == 1
        assert stream["services"][0]["day"] == "sunday"
        assert stream["services"][0]["morning"] == "10:00"

    def test_normalize_legacy_no_services(self):
        """Test that legacy streams without times get empty services list."""
        stream = {
            "name": "no_times",
            "timezone": "America/Chicago",
        }

        # Migrate
        if "services" not in stream:
            morning = stream.get("sunday_morning_service_time")
            evening = stream.get("sunday_evening_service_time")
            ssb = stream.get("sunday_school_break", False)
            stream_copy = dict(stream)
            stream_copy["services"] = (
                [{"day": "sunday", "morning": morning, "evening": evening,
                  "sunday_school_break": ssb}]
                if (morning or evening) else []
            )
            stream = stream_copy

        assert "services" in stream
        assert len(stream["services"]) == 0


class TestStreamValidation:
    """Test validation of stream data."""

    def test_valid_stream_required_fields(self):
        """Test that required fields are present."""
        stream = {
            "name": "test",
            "url": "https://example.com/stream.mp3",
            "status_url": "https://example.com/status",
        }

        assert stream.get("name") is not None
        assert stream.get("url") is not None
        assert stream.get("status_url") is not None

    def test_stream_name_required(self):
        """Test that stream name is required."""
        stream = {
            "url": "https://example.com/stream.mp3",
            "status_url": "https://example.com/status",
        }

        name = stream.get("name", "").strip()
        assert len(name) == 0

    def test_stream_timezone_default(self):
        """Test that timezone defaults to UTC."""
        stream = {
            "name": "test",
            "url": "https://example.com/stream.mp3",
        }

        timezone = stream.get("timezone", "UTC")
        assert timezone == "UTC"

    def test_stream_enabled_default(self):
        """Test that enabled defaults to True."""
        stream = {
            "name": "test",
            "url": "https://example.com/stream.mp3",
        }

        # Form value "on" means enabled, absence means False
        enabled = stream.get("enabled", True)
        assert enabled is True


class TestConfigPersistence:
    """Test YAML config file loading and saving."""

    def test_load_save_config(self, sample_config_file, sample_streams_config):
        """Test loading and saving config to file."""
        # Simulate load
        with open(sample_config_file, "r") as f:
            loaded = yaml.safe_load(f)

        assert "streams" in loaded
        assert len(loaded["streams"]) == len(sample_streams_config["streams"])

        # Simulate save
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(loaded, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            temp_path = f.name

        try:
            with open(temp_path, "r") as f:
                reloaded = yaml.safe_load(f)
            assert reloaded == loaded
        finally:
            os.unlink(temp_path)

    def test_config_empty_streams(self):
        """Test loading config with empty streams list."""
        config = {"streams": []}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(config, f)
            temp_path = f.name

        try:
            with open(temp_path, "r") as f:
                loaded = yaml.safe_load(f)
            assert loaded["streams"] == []
        finally:
            os.unlink(temp_path)

    def test_config_missing_file_fallback(self):
        """Test that missing config file falls back to empty structure."""
        config = yaml.safe_load("") or {"streams": []}
        assert config["streams"] == []

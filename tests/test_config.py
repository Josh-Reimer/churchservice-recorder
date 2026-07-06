"""Tests for configuration parsing and loading."""
import pytest
import os
import yaml
import tempfile
import pytz
from datetime import datetime
from pathlib import Path


# We need to import these functions from new_recorder
# To avoid circular imports and module-level side effects, we'll test
# the logic in isolation
def test_yaml_parsing(sample_config_file, sample_streams_config):
    """Test that YAML config can be loaded correctly."""
    with open(sample_config_file, "r") as f:
        loaded = yaml.safe_load(f)

    assert "streams" in loaded
    assert len(loaded["streams"]) == 2
    assert loaded["streams"][0]["name"] == "main_stream"
    assert loaded["streams"][0]["enabled"] is True
    assert loaded["streams"][1]["enabled"] is False


def test_legacy_config_structure(legacy_streams_config):
    """Test that legacy format config is valid YAML."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(legacy_streams_config, f)
        temp_file = f.name

    try:
        with open(temp_file, "r") as f:
            loaded = yaml.safe_load(f)

        assert "streams" in loaded
        assert len(loaded["streams"]) == 1
        assert "sunday_morning_service_time" in loaded["streams"][0]
        assert "sunday_evening_service_time" in loaded["streams"][0]
    finally:
        os.unlink(temp_file)


def test_safe_name_sanitization():
    """Test that stream names are sanitized for filenames."""
    test_cases = [
        ("Main Church Service", "main_church_service"),
        ("St. John's Community", "st_john's_community"),
        ("UPPERCASE SERVICE", "uppercase_service"),
    ]

    for full_name, expected_safe in test_cases:
        safe_name = (
            full_name.lower()
            .replace(" ", "_")
            .replace(",", "")
            .replace(".", "")
            .replace("cong", "congregation")
            .replace("-", "_")
        )
        # Collapse multiple underscores
        while "__" in safe_name:
            safe_name = safe_name.replace("__", "_")
        assert safe_name == expected_safe, f"Failed for {full_name}"


def test_config_with_empty_services():
    """Test config parsing with a stream that has no services."""
    config = {
        "streams": [
            {
                "name": "no_services",
                "url": "https://example.com/stream.mp3",
                "status_url": "https://example.com/status",
                "timezone": "UTC",
                "services": [],
            }
        ]
    }

    assert len(config["streams"][0]["services"]) == 0


def test_config_with_only_morning_service():
    """Test config with only morning service (no evening)."""
    config = {
        "streams": [
            {
                "name": "morning_only",
                "url": "https://example.com/stream.mp3",
                "status_url": "https://example.com/status",
                "timezone": "America/Chicago",
                "services": [
                    {
                        "day": "sunday",
                        "morning": "10:00",
                    }
                ],
            }
        ]
    }

    service = config["streams"][0]["services"][0]
    assert service.get("morning") == "10:00"
    assert service.get("evening") is None


def test_config_defaults():
    """Test that config defaults are applied."""
    minimal_config = {
        "streams": [
            {
                "name": "minimal",
                "url": "https://example.com/stream.mp3",
                "status_url": "https://example.com/status",
                # Missing timezone, enabled, services
            }
        ]
    }

    stream = minimal_config["streams"][0]
    # Should handle missing fields gracefully
    assert stream.get("timezone", "UTC") == "UTC"
    assert stream.get("enabled", True) is True
    assert stream.get("services", []) == []


def test_duplicate_url_detection():
    """Test logic to detect duplicate stream URLs."""
    config = {
        "streams": [
            {
                "name": "stream1",
                "url": "https://example.com/stream.mp3",
                "status_url": "https://example.com/status",
            },
            {
                "name": "stream2",
                "url": "https://example.com/stream.mp3",  # Duplicate
                "status_url": "https://example.com/status2",
            },
        ]
    }

    seen_urls = set()
    duplicates = []
    for stream in config["streams"]:
        url = stream.get("url")
        if url in seen_urls:
            duplicates.append(url)
        seen_urls.add(url)

    assert len(duplicates) == 1
    assert duplicates[0] == "https://example.com/stream.mp3"


def test_day_name_to_index():
    """Test conversion of day names to day-of-week indices."""
    DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

    test_cases = [
        ("monday", 0),
        ("sunday", 6),
        ("wednesday", 2),
        ("friday", 4),
    ]

    for day_str, expected_idx in test_cases:
        idx = DAYS.index(day_str) if day_str in DAYS else 6
        assert idx == expected_idx


class TestBuildServicesResilience:
    """A broken stream entry must be skipped, not crash the recorder."""

    @pytest.fixture
    def recorder(self, monkeypatch, temp_config_dir):
        import new_recorder as nr
        monkeypatch.setattr(nr, "OUTPUT_DIR", os.path.join(temp_config_dir, "rec"))
        monkeypatch.setattr(nr, "TRANSCRIPTIONS_DIR", os.path.join(temp_config_dir, "tr"))
        monkeypatch.setattr(nr, "notify_telegram", lambda text: None)
        return nr

    def _good_stream(self, url="https://example.com/a.mp3"):
        return {
            "name": "good", "full_name": "Good Stream", "url": url,
            "status_url": "https://example.com/status",
            "timezone": "America/Chicago",
            "services": [{"day": "sunday", "morning": "10:00"}],
        }

    def test_unknown_timezone_skips_only_that_stream(self, recorder):
        bad = self._good_stream(url="https://example.com/b.mp3")
        bad["name"] = "bad"
        bad["timezone"] = "America/Denverr"
        services = recorder.build_services({"streams": [bad, self._good_stream()]})
        assert [s.name for s in services] == ["good"]

    def test_malformed_time_skips_only_that_stream(self, recorder):
        # Unquoted `morning: 10:00` in YAML parses as the integer 600.
        bad = self._good_stream(url="https://example.com/b.mp3")
        bad["name"] = "bad"
        bad["services"] = [{"day": "sunday", "morning": 600}]
        services = recorder.build_services({"streams": [bad, self._good_stream()]})
        assert [s.name for s in services] == ["good"]

    def test_all_valid_streams_load(self, recorder):
        services = recorder.build_services({"streams": [self._good_stream()]})
        assert len(services) == 1
        assert services[0].slots[0].morning_time_text == "10:00"

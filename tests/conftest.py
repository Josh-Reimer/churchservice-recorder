"""Pytest fixtures and configuration for church recorder tests."""
import pytest
import tempfile
import os
import sys
import yaml
import pytz
from datetime import datetime
from pathlib import Path

# Add parent directory to path so we can import new_recorder
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def temp_config_dir():
    """Create a temporary directory for config files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def sample_streams_config():
    """Return a sample streams.yml configuration."""
    return {
        "streams": [
            {
                "name": "main_stream",
                "full_name": "Main Church Service",
                "url": "https://example.com/stream.mp3",
                "status_url": "https://example.com/status",
                "timezone": "America/Chicago",
                "enabled": True,
                "services": [
                    {
                        "day": "sunday",
                        "morning": "10:00",
                        "evening": "18:00",
                        "sunday_school_break": True,
                    },
                    {
                        "day": "wednesday",
                        "evening": "19:00",
                    },
                ],
            },
            {
                "name": "second_stream",
                "full_name": "Second Congregation Service",
                "url": "https://example.com/stream2.mp3",
                "status_url": "https://example.com/status2",
                "timezone": "America/New_York",
                "enabled": False,
                "services": [
                    {
                        "day": "sunday",
                        "morning": "09:30",
                    },
                ],
            },
        ]
    }


@pytest.fixture
def legacy_streams_config():
    """Return a legacy format streams.yml configuration."""
    return {
        "streams": [
            {
                "name": "legacy_stream",
                "timezone": "America/Chicago",
                "sunday_morning_service_time": "10:00",
                "sunday_evening_service_time": "18:00",
                "sunday_school_break": False,
            }
        ]
    }


@pytest.fixture
def sample_config_file(temp_config_dir, sample_streams_config):
    """Write sample config to a temporary file and return the path."""
    config_file = os.path.join(temp_config_dir, "streams.yml")
    with open(config_file, "w") as f:
        yaml.dump(sample_streams_config, f)
    return config_file


@pytest.fixture
def chicago_tz():
    """Return Chicago timezone."""
    return pytz.timezone("America/Chicago")


@pytest.fixture
def new_york_tz():
    """Return New York timezone."""
    return pytz.timezone("America/New_York")


@pytest.fixture
def utc_tz():
    """Return UTC timezone."""
    return pytz.UTC

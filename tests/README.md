# ByteWorship Recorder Test Suite

This directory contains unit tests for the church service recorder application.

## Running Tests

### Install test dependencies
```bash
pip install -r requirements-test.txt
```

### Run all tests
```bash
pytest tests/
```

### Run with verbose output
```bash
pytest tests/ -v
```

### Run specific test file
```bash
pytest tests/test_models.py
```

### Run specific test class
```bash
pytest tests/test_scheduling.py::TestLastFiredTracking
```

### Run specific test function
```bash
pytest tests/test_scheduling.py::TestLastFiredTracking::test_fire_once_per_day_morning
```

### Run tests with coverage
```bash
pytest tests/ --cov=. --cov-report=html
```

## Test Organization

### `test_models.py`
Tests for core data models:
- `ServiceSlot` — initialization, tracking last_fired separately for morning/evening
- `StreamInfo` — initialization, enabled/disabled state, multiple slots

### `test_config.py`
Tests for configuration parsing and loading:
- YAML structure validation
- Legacy format support (old `sunday_*_service_time` fields)
- Safe name sanitization for output directories
- Default values
- Duplicate URL detection
- Day-of-week name mapping

### `test_time_conversion.py`
Tests for timezone and time handling:
- Converting time strings (HH:MM) to tz-aware datetimes
- Cross-timezone conversion (Chicago, New York, Denver, UTC)
- Daylight Saving Time edge cases (spring forward, fall back)
- Trigger window logic (checking if current time is within service trigger window)

### `test_scheduling.py`
Tests for scheduling logic:
- Correct weekday triggering
- Wrong weekday skipping
- `last_fired` tracking to prevent double-triggering
- Separate morning/evening tracking
- Multiple slots per stream
- Sunday school break flag handling
- Enabled/disabled stream filtering

## What's Tested

✅ **Data Models**
- ServiceSlot creation and state management
- StreamInfo creation and attributes
- last_fired tracking (prevent duplicates)

✅ **Configuration**
- YAML loading and parsing
- Legacy format compatibility
- Safe filename generation from stream names
- Default values

✅ **Scheduling Logic**
- Correct weekday detection
- Time-based triggering within service windows
- Once-per-day firing with date tracking
- Enabled/disabled stream filtering
- Multiple service times per stream
- Sunday school break handling

✅ **Timezone/Time Handling**
- Converting local times to system timezone
- Cross-timezone scheduling
- DST transitions
- Trigger window boundaries

## What's NOT Tested (Integration/E2E)

These require external dependencies and are better suited for integration tests:
- Config hot-reload from disk
- Recording flow (ffmpeg, stream polling)
- Transcription workflow (Whisper)
- Flask web routes
- Telegram notifications
- Thread pool execution

See `tests/test_*_integration.py` (when added) for integration tests that use real file I/O and mocked external services.

## Adding New Tests

When adding new functionality:

1. **Unit tests** — test new functions/classes with mocked dependencies
2. **Fixtures** — add reusable fixtures to `conftest.py`
3. **Organization** — group related tests in classes (e.g., `TestScheduling`)

Example:
```python
# tests/test_new_feature.py
import pytest
from new_recorder import NewClass

class TestNewClass:
    def test_something(self, chicago_tz):
        obj = NewClass(timezone=chicago_tz)
        assert obj.property == expected_value
```

## Continuous Testing

To run tests on save (requires watchdog):
```bash
pip install watchdog
ptw  # runs pytest-watch
```

# ByteWorship Recorder - Test Suite Setup

## Overview

A comprehensive pytest unit test suite has been set up for the ByteWorship Recorder application. The suite includes 50 passing tests covering core functionality, data models, scheduling logic, timezone handling, and configuration management.

## Quick Start

```bash
# Install test dependencies (one time)
pip install -r requirements-test.txt

# Run all tests
pytest tests/

# Run with verbose output
pytest tests/ -v

# Run with coverage report
pytest tests/ --cov=. --cov-report=html
```

## Test Files Created

| File | Tests | Coverage |
|------|-------|----------|
| `tests/conftest.py` | Fixtures & config | Pytest infrastructure |
| `tests/test_models.py` | 6 tests | `StreamInfo`, `ServiceSlot` data models |
| `tests/test_config.py` | 8 tests | YAML parsing, schema validation, filename sanitization |
| `tests/test_time_conversion.py` | 13 tests | Timezone conversion, DST handling, trigger window logic |
| `tests/test_scheduling.py` | 13 tests | Service scheduling, last_fired tracking, multi-slot handling |
| `tests/test_webserver.py` | 10 tests | Form parsing, config normalization, validation |
| `tests/README.md` | — | Testing guide and documentation |
| `pytest.ini` | — | Pytest configuration |
| `requirements-test.txt` | — | Test dependencies |

**Total: 50 passing tests ✅**

## Test Coverage by Component

### Data Models (`test_models.py`)
- ✅ ServiceSlot initialization and state management
- ✅ StreamInfo creation with multiple slots
- ✅ last_fired tracking (prevents duplicate recordings)
- ✅ Enabled/disabled stream states

### Configuration (`test_config.py`)
- ✅ YAML structure validation
- ✅ Legacy format support (old `sunday_*_service_time` fields auto-migrate)
- ✅ Safe filename generation from stream names
- ✅ Default values for missing fields
- ✅ Duplicate URL detection
- ✅ Day-of-week name to index mapping

### Time & Timezone Handling (`test_time_conversion.py`)
- ✅ Converting HH:MM strings to tz-aware datetimes
- ✅ Cross-timezone conversion (Chicago, New York, Denver, UTC)
- ✅ Daylight Saving Time edge cases (spring forward, fall back)
- ✅ Service trigger window logic (60-second fire window)
- ✅ Midnight and afternoon edge cases

### Scheduling Logic (`test_scheduling.py`)
- ✅ Correct weekday detection for services
- ✅ Wrong weekday skipping
- ✅ Once-per-day firing with date tracking
- ✅ Separate morning/evening tracking
- ✅ Multiple service slots per stream
- ✅ Sunday school break flag handling
- ✅ Enabled/disabled stream filtering

### Webserver Utilities (`test_webserver.py`)
- ✅ Form parsing (single and multiple slots)
- ✅ Stream normalization (legacy → new format)
- ✅ Required field validation
- ✅ Default values (timezone, enabled state)
- ✅ YAML config persistence (load/save)

## Dependencies

Test suite uses:
- **pytest** (7.0+) — test framework
- **pytest-mock** (3.10+) — mocking utilities
- **pytest-cov** (4.0+) — coverage reporting
- **freezegun** (1.2+) — time mocking (future use)
- **pyyaml** (6.0+) — config parsing
- **pytz** (2023.3+) — timezone handling

## Running Tests

### All tests
```bash
pytest tests/
```

### Specific test file
```bash
pytest tests/test_models.py -v
```

### Specific test class
```bash
pytest tests/test_scheduling.py::TestLastFiredTracking -v
```

### Specific test
```bash
pytest tests/test_time_conversion.py::TestTimeConversion::test_convert_time_basic -v
```

### With coverage
```bash
pytest tests/ --cov=. --cov-report=html
open htmlcov/index.html
```

## What's Tested (Unit Tests)

✅ **Core Logic** — data models, scheduling, timezone conversions  
✅ **Configuration** — YAML parsing, format migration, validation  
✅ **Webserver** — form handling, config operations, validation  

## What's NOT Tested Yet (Integration/E2E)

These require external dependencies and are better suited for integration tests:
- Config hot-reload from disk
- Recording flow (ffmpeg polling, stream status checks)
- Transcription workflow (Whisper model, GPU)
- Flask route handlers (require full app context, file I/O)
- Telegram notifications
- Thread pool execution and background workers

## Adding New Tests

When adding features, write tests first (TDD) or immediately after:

```python
# tests/test_my_feature.py
import pytest
from new_recorder import MyNewClass

class TestMyFeature:
    def test_something(self, chicago_tz):
        obj = MyNewClass(timezone=chicago_tz)
        assert obj.result == expected
```

Add fixtures to `conftest.py` for reusability:
```python
# conftest.py
@pytest.fixture
def my_fixture():
    return some_value
```

## Test Execution

```
======================== 50 passed, 1 warning in 0.19s =========================

✅ All tests pass in ~0.2 seconds
```

## Next Steps

1. **Run tests locally** — verify they work in your environment
2. **Add to CI/CD** — configure GitHub Actions / other CI to run on every push
3. **Expand coverage** — add integration tests for recording flow, Flask routes
4. **Mock external services** — add mocks for Whisper, ffmpeg, Telegram API
5. **Test edge cases** — add tests for error conditions, network failures, etc.

## Resources

- [Pytest docs](https://docs.pytest.org/)
- [pytest-mock docs](https://pytest-mock.readthedocs.io/)
- [freezegun docs](https://freezegun.readthedocs.io/)
- See `tests/README.md` for detailed testing guide

# Testing Procedure for ByteWorship Recorder

This document describes how to run, write, and maintain tests for the ByteWorship Recorder application.

## Table of Contents

1. [Quick Start](#quick-start)
2. [Setup](#setup)
3. [Running Tests](#running-tests)
4. [Test Organization](#test-organization)
5. [Writing New Tests](#writing-new-tests)
6. [Continuous Integration](#continuous-integration)
7. [Troubleshooting](#troubleshooting)

---

## Quick Start

### One-time setup
```bash
pip install -r requirements-test.txt
```

### Run all tests
```bash
pytest tests/
```

### Expected output
```
======================== 50 passed in 0.16s =========================
```

---

## Setup

### Prerequisites

- Python 3.7+
- Virtual environment (recommended)
- Project dependencies installed

### Install Test Dependencies

```bash
# From project root
pip install -r requirements-test.txt
```

**Installed packages:**
- `pytest` (7.0+) — test framework
- `pytest-mock` (3.10+) — mocking utilities
- `pytest-cov` (4.0+) — coverage reporting
- `freezegun` (1.2+) — time mocking
- `pyyaml` (6.0+) — config parsing
- `pytz` (2023.3+) — timezone handling

---

## Running Tests

### Basic Usage

#### Run all tests
```bash
pytest tests/
```

#### Run with verbose output
```bash
pytest tests/ -v
```

#### Run tests matching a pattern
```bash
# Run only scheduling tests
pytest tests/test_scheduling.py

# Run only a specific test class
pytest tests/test_scheduling.py::TestLastFiredTracking

# Run a specific test
pytest tests/test_scheduling.py::TestLastFiredTracking::test_fire_once_per_day_morning
```

### Advanced Usage

#### Run with coverage report
```bash
pytest tests/ --cov=. --cov-report=html
open htmlcov/index.html
```

#### Run with short summary
```bash
pytest tests/ -q
```

#### Run with detailed output
```bash
pytest tests/ -vv
```

#### Stop on first failure
```bash
pytest tests/ -x
```

#### Fail on warnings
```bash
pytest tests/ --strict-markers
```

#### Run specific test markers
```bash
# Show available markers
pytest tests/ --markers

# Run only slow tests
pytest tests/ -m slow

# Run everything except slow tests
pytest tests/ -m "not slow"
```

---

## Test Organization

### Directory Structure

```
tests/
├── conftest.py                 # Shared fixtures and configuration
├── test_config.py              # Configuration parsing & validation
├── test_models.py              # Data models (StreamInfo, ServiceSlot)
├── test_scheduling.py          # Scheduling logic
├── test_time_conversion.py     # Timezone handling
├── test_webserver.py           # Webserver utilities
├── README.md                   # Detailed testing guide
└── EXAMPLE_TEST.md             # How to write tests
```

### Test File Naming

- Test files: `test_*.py`
- Test classes: `Test*`
- Test methods: `test_*`

Example:
```python
# tests/test_feature.py
class TestFeatureName:
    def test_specific_behavior(self):
        pass
```

### Fixture Organization

Common fixtures are in `conftest.py`:
- `chicago_tz` — Chicago timezone object
- `new_york_tz` — New York timezone object
- `utc_tz` — UTC timezone object
- `sample_streams_config` — sample config dict
- `sample_config_file` — temporary config file
- `temp_config_dir` — temporary directory

Usage:
```python
def test_something(self, chicago_tz, sample_config_file):
    # chicago_tz and sample_config_file are automatically provided
    pass
```

---

## Writing New Tests

### Basic Test Structure

```python
# tests/test_feature.py
import pytest
from new_recorder import MyClass

class TestMyFeature:
    """Test suite for my feature."""
    
    def test_basic_behavior(self):
        """Test that the feature works."""
        obj = MyClass()
        assert obj.result == expected_value
    
    def test_error_handling(self):
        """Test error conditions."""
        with pytest.raises(ValueError):
            MyClass(invalid_input)
```

### Using Fixtures

```python
def test_with_timezone(self, chicago_tz):
    """Test that uses a timezone fixture."""
    result = convert_time("10:00", chicago_tz)
    assert result is not None

def test_with_config(self, sample_config_file):
    """Test that uses a config file fixture."""
    with open(sample_config_file, "r") as f:
        config = yaml.safe_load(f)
    assert "streams" in config
```

### Mocking External Dependencies

```python
from unittest.mock import patch, Mock

def test_with_mocked_service(self):
    """Test with mocked external service."""
    with patch('new_recorder.requests.get') as mock_get:
        mock_get.return_value.json.return_value = {"status": 1}
        
        result = stream_available("https://example.com/status")
        assert result is True
```

### Parameterized Tests

```python
@pytest.mark.parametrize("input,expected", [
    ("10:00", 10),
    ("14:30", 14),
    ("23:59", 23),
])
def test_various_times(self, input, expected):
    """Test multiple inputs."""
    result = convert_time(input)
    assert result.hour == expected
```

### Testing Exceptions

```python
def test_raises_error_on_invalid_input(self):
    """Test that proper exceptions are raised."""
    with pytest.raises(ValueError, match="Invalid timezone"):
        build_services({"streams": [{"timezone": "Invalid/Zone"}]})
```

### Adding New Test Files

1. Create `tests/test_feature.py`
2. Import what you need
3. Create test classes and functions
4. Run: `pytest tests/test_feature.py -v`

Example:
```python
# tests/test_my_new_feature.py
import pytest
from new_recorder import my_new_function

class TestMyNewFeature:
    def test_something(self):
        result = my_new_function()
        assert result is True
```

---

## Coverage Analysis

### Generate Coverage Report

```bash
# Terminal report
pytest tests/ --cov=. --cov-report=term-missing

# HTML report
pytest tests/ --cov=. --cov-report=html
open htmlcov/index.html

# XML report (for CI/CD)
pytest tests/ --cov=. --cov-report=xml
```

### Interpreting Coverage

Coverage percentage = (lines executed / total lines) × 100

Example output:
```
tests/test_models.py ....                              [ 28%]
tests/test_config.py ........                          [ 16%]

Name          Stmts   Miss  Cover
-----------------------------------
new_recorder    523    145    72%
webserver       287     92    68%
```

**Target**: 70%+ coverage for unit tests

---

## Continuous Integration

### Running Tests in CI/CD

Add to your CI/CD pipeline (GitHub Actions, GitLab CI, etc.):

```yaml
# Example: .github/workflows/test.yml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
      - run: pip install -r requirements-test.txt
      - run: pytest tests/ --cov=.
```

### Pre-commit Hook

Automatically run tests before committing:

```bash
# .git/hooks/pre-commit (make executable: chmod +x)
#!/bin/bash
pytest tests/ -q || exit 1
```

---

## Test Lifecycle

### Before Each Test

Fixtures run and provide test data:
```python
@pytest.fixture
def setup_data():
    # This runs BEFORE each test that uses this fixture
    data = {"key": "value"}
    yield data
    # Optional cleanup after test
```

### During Test

Test executes with provided fixtures and assertions.

### After Each Test

Cleanup code runs (e.g., deleting temp files, clearing state).

---

## Best Practices

### ✅ Do's

- ✅ **One assertion per test** — clear failure messages
  ```python
  def test_value(self):
      assert obj.x == expected_x
  ```

- ✅ **Descriptive names** — explains what's being tested
  ```python
  def test_fire_once_per_day_morning(self):  # Good
  def test_fire(self):  # Bad
  ```

- ✅ **Arrange, Act, Assert** — clear test structure
  ```python
  def test_something(self):
      # Arrange
      obj = MyClass()
      # Act
      result = obj.do_something()
      # Assert
      assert result == expected
  ```

- ✅ **Use fixtures** — avoid duplication
  ```python
  def test_with_timezone(self, chicago_tz):  # Reusable fixture
      pass
  ```

- ✅ **Mock external services** — keep tests isolated
  ```python
  with patch('requests.get') as mock:
      mock.return_value.json.return_value = {...}
  ```

- ✅ **Test edge cases** — None, empty, boundaries
  ```python
  def test_with_none_value(self):
      assert convert_time(None) is None
  ```

### ❌ Don'ts

- ❌ **Multiple assertions** — confusing failures
  ```python
  # Bad
  assert obj.x == 1
  assert obj.y == 2
  assert obj.z == 3
  ```

- ❌ **Vague names** — unclear purpose
  ```python
  def test_it(self):  # Bad
  def test_convert_time_with_chicago_timezone(self):  # Good
  ```

- ❌ **Test interdependence** — tests should run in any order
  ```python
  # Bad: test_b depends on test_a running first
  def test_a(self):
      global state
      state = 1
  
  def test_b(self):
      assert state == 1  # Fails if test_a doesn't run first
  ```

- ❌ **Real I/O in tests** — slow and unreliable
  ```python
  # Bad: writes to actual filesystem
  f = open("/app/config/streams.yml")
  
  # Good: use temp file fixture
  def test_with_config(self, sample_config_file):
      pass
  ```

- ❌ **Ignoring errors** — catch and test them
  ```python
  # Bad: ignoring exceptions
  try:
      some_function()
  except:
      pass  # Oops!
  
  # Good: explicitly test exceptions
  with pytest.raises(ValueError):
      some_function(bad_input)
  ```

---

## Troubleshooting

### Tests won't run

**Problem**: `ModuleNotFoundError: No module named 'new_recorder'`

**Solution**: Tests are run from project root. Make sure you're in the right directory:
```bash
cd /path/to/churchservice-recorder
pytest tests/
```

### Import errors

**Problem**: `ImportError: cannot import name 'X'`

**Solution**: 
1. Verify the import path is correct
2. Check that the module/class is defined
3. Check `conftest.py` adds the parent directory to `sys.path`

### Fixture not found

**Problem**: `fixture 'chicago_tz' not found`

**Solution**:
1. Verify fixture is defined in `conftest.py`
2. Verify spelling matches exactly
3. Run from project root: `pytest tests/`

### Test passes locally but fails in CI

**Problem**: Environmental differences

**Solution**:
1. Check Python version: `python --version`
2. Check timezone: `echo $TZ`
3. Mock system calls instead of relying on environment

### Tests are slow

**Problem**: Tests take too long

**Solution**:
1. Use `-x` to stop on first failure
2. Use `-k pattern` to run only specific tests
3. Mock I/O operations (file reading, HTTP requests)
4. Profile with `pytest --durations=10`

---

## Adding Tests to Your Workflow

### 1. Before Starting a Feature

Write tests first (TDD):
```bash
# Create tests/test_my_feature.py
# Run: pytest tests/test_my_feature.py
# Tests fail (expected)
# Implement feature
# Tests pass
```

### 2. Before Committing

Run all tests:
```bash
pytest tests/ -v
```

Verify nothing broke:
```bash
pytest tests/ --tb=short
```

### 3. Before Creating a PR

Check coverage:
```bash
pytest tests/ --cov=. --cov-report=term-missing
```

Run full suite:
```bash
pytest tests/
```

### 4. Review Checklist

- ✅ New feature has corresponding tests
- ✅ All tests pass locally
- ✅ Coverage hasn't decreased
- ✅ No test interdependencies
- ✅ Tests are documented (docstrings)

---

## Resources

- **Pytest docs**: https://docs.pytest.org/
- **pytest-mock**: https://pytest-mock.readthedocs.io/
- **freezegun**: https://freezegun.readthedocs.io/
- **Testing guide**: `tests/README.md`
- **Example test**: `tests/EXAMPLE_TEST.md`

---

## Maintenance

### Regular Tasks

| Task | Frequency | Command |
|------|-----------|---------|
| Run tests | Before each commit | `pytest tests/ -v` |
| Check coverage | Weekly | `pytest tests/ --cov=.` |
| Update dependencies | Monthly | `pip install -r requirements-test.txt --upgrade` |
| Review test quality | Quarterly | Review test files and refactor as needed |

### Updating Test Dependencies

```bash
# Check for updates
pip list --outdated | grep pytest

# Update all test packages
pip install -r requirements-test.txt --upgrade

# Update specific package
pip install --upgrade pytest
```

---

## Summary

| Action | Command |
|--------|---------|
| **Run all tests** | `pytest tests/` |
| **Run with verbose** | `pytest tests/ -v` |
| **Run specific test** | `pytest tests/test_file.py::TestClass::test_name` |
| **Check coverage** | `pytest tests/ --cov=.` |
| **Stop on failure** | `pytest tests/ -x` |
| **Run matching pattern** | `pytest tests/ -k pattern` |

---

## Contact & Support

For questions about testing:
1. Check `tests/README.md` for detailed guide
2. Look at `tests/EXAMPLE_TEST.md` for examples
3. Review existing tests in `tests/test_*.py` for patterns
4. Run pytest help: `pytest --help`

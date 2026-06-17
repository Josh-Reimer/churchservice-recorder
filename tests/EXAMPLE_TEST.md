# Example: Writing Your First Test

This guide shows how to write a test for a new feature in the ByteWorship Recorder.

## Scenario: Testing a new "silence detection" feature

Imagine we're adding a function to detect if a recording has too much silence and should be flagged.

### Step 1: Define the function signature

```python
# In new_recorder.py
def detect_excessive_silence(audio_file_path, silence_threshold_db=-40, min_duration_seconds=30):
    """
    Check if audio file has excessive silence.
    
    Returns: True if file has silence >= min_duration_seconds
    """
    pass
```

### Step 2: Write the test first (TDD)

Create a test file:

```python
# tests/test_silence_detection.py
import pytest
import tempfile
import os
from unittest.mock import Mock, patch
from new_recorder import detect_excessive_silence


class TestSilenceDetection:
    """Test silence detection in audio files."""

    def test_detect_excessive_silence_present(self):
        """Test that function returns True when silence is detected."""
        # Mock the audio processing
        with patch('new_recorder.detect_silence_segments') as mock_detect:
            mock_detect.return_value = [
                {"start": 10.0, "end": 50.0}  # 40 seconds of silence
            ]
            
            result = detect_excessive_silence(
                "recording.mp3",
                silence_threshold_db=-40,
                min_duration_seconds=30
            )
            
            assert result is True

    def test_detect_no_excessive_silence(self):
        """Test that function returns False when silence is not excessive."""
        with patch('new_recorder.detect_silence_segments') as mock_detect:
            mock_detect.return_value = [
                {"start": 10.0, "end": 15.0}  # Only 5 seconds of silence
            ]
            
            result = detect_excessive_silence(
                "recording.mp3",
                silence_threshold_db=-40,
                min_duration_seconds=30
            )
            
            assert result is False

    def test_detect_multiple_silence_segments(self):
        """Test detection with multiple silence segments."""
        with patch('new_recorder.detect_silence_segments') as mock_detect:
            mock_detect.return_value = [
                {"start": 10.0, "end": 20.0},   # 10 seconds
                {"start": 50.0, "end": 90.0},   # 40 seconds
            ]
            
            result = detect_excessive_silence(
                "recording.mp3",
                min_duration_seconds=30
            )
            
            # Total of 50 seconds > 30 seconds threshold
            assert result is True

    def test_file_not_found(self):
        """Test handling of missing audio file."""
        with pytest.raises(FileNotFoundError):
            detect_excessive_silence("nonexistent.mp3")
```

### Step 3: Run the tests (they'll fail initially)

```bash
$ pytest tests/test_silence_detection.py -v

tests/test_silence_detection.py::TestSilenceDetection::test_detect_excessive_silence_present FAILED
tests/test_silence_detection.py::TestSilenceDetection::test_detect_no_excessive_silence FAILED
tests/test_silence_detection.py::TestSilenceDetection::test_detect_multiple_silence_segments FAILED
tests/test_silence_detection.py::TestSilenceDetection::test_file_not_found FAILED

FAILED - 4 passed in 0.05s
```

### Step 4: Implement the function

```python
# In new_recorder.py
def detect_silence_segments(audio_file_path, threshold_db=-40):
    """Helper to get silence segments from audio."""
    # Implementation using librosa or similar
    pass


def detect_excessive_silence(audio_file_path, silence_threshold_db=-40, min_duration_seconds=30):
    """Check if audio file has excessive silence."""
    if not os.path.exists(audio_file_path):
        raise FileNotFoundError(f"Audio file not found: {audio_file_path}")
    
    segments = detect_silence_segments(audio_file_path, silence_threshold_db)
    
    for segment in segments:
        duration = segment["end"] - segment["start"]
        if duration >= min_duration_seconds:
            return True
    
    return False
```

### Step 5: Run tests again (they should pass)

```bash
$ pytest tests/test_silence_detection.py -v

tests/test_silence_detection.py::TestSilenceDetection::test_detect_excessive_silence_present PASSED
tests/test_silence_detection.py::TestSilenceDetection::test_detect_no_excessive_silence PASSED
tests/test_silence_detection.py::TestSilenceDetection::test_detect_multiple_silence_segments PASSED
tests/test_silence_detection.py::TestSilenceDetection::test_file_not_found PASSED

======================== 4 passed in 0.05s =========================
```

## Key Testing Patterns

### 1. Use fixtures for reusable setup

```python
# In conftest.py
@pytest.fixture
def sample_audio_path():
    """Create a temporary audio file for testing."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        # Write minimal MP3 data
        f.write(b"ID3...")
        temp_path = f.name
    
    yield temp_path
    
    # Cleanup
    os.unlink(temp_path)


# Use in tests
def test_with_real_file(sample_audio_path):
    result = detect_excessive_silence(sample_audio_path)
    assert isinstance(result, bool)
```

### 2. Mock external dependencies

```python
from unittest.mock import patch, Mock

def test_with_mock_transcription():
    with patch('new_recorder.whisper.load_model') as mock_load:
        mock_model = Mock()
        mock_model.transcribe.return_value = {"text": "test transcription"}
        mock_load.return_value = mock_model
        
        # Test transcription code
        result = transcribe_audio("file.mp3")
        assert result == "test transcription"
```

### 3. Test error conditions

```python
import pytest

def test_invalid_timezone():
    with pytest.raises(pytz.exceptions.UnknownTimeZoneError):
        build_services({"streams": [{"timezone": "Invalid/Zone"}]})

def test_missing_required_field():
    with pytest.raises(KeyError):
        stream = StreamInfo(name=None, ...)  # name is required
```

### 4. Use parameterize for multiple similar tests

```python
import pytest

@pytest.mark.parametrize("time_str,expected_hour", [
    ("10:00", 10),
    ("14:30", 14),
    ("23:59", 23),
])
def test_convert_time_various_times(chicago_tz, time_str, expected_hour):
    result = convert_time(time_str, chicago_tz)
    assert result.hour == expected_hour
```

### 5. Group related tests in classes

```python
class TestRecordingFlow:
    """All tests related to the recording flow."""
    
    def test_start_recording(self):
        pass
    
    def test_stop_recording(self):
        pass
    
    def test_recording_with_error(self):
        pass
```

## Running Your Tests

```bash
# Run your new tests
pytest tests/test_silence_detection.py -v

# Run with coverage
pytest tests/test_silence_detection.py --cov=new_recorder

# Run all tests (your new ones + existing)
pytest tests/ -v
```

## Tips

1. **One assertion per test** — keeps tests focused and failures clear
2. **Descriptive test names** — `test_fire_once_per_day_morning` is better than `test_fire`
3. **Use fixtures** — reduce duplication, centralize test data
4. **Mock external APIs** — Telegram, Whisper, ffmpeg, HTTP calls
5. **Test edge cases** — None values, empty lists, timezone boundaries
6. **Keep tests fast** — mock I/O, avoid real files when possible

## See Also

- `tests/README.md` — full testing guide
- `tests/test_*.py` — existing tests as examples
- [pytest docs](https://docs.pytest.org/)

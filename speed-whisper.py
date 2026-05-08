"""
transcribe_mp3.py — Batch MP3 transcription using faster-whisper

Install dependencies:
    pip install faster-whisper

Usage:
    python transcribe_mp3.py                        # transcribes all *.mp3 in current dir
    python transcribe_mp3.py file1.mp3 file2.mp3    # transcribes specific files
    python transcribe_mp3.py --model large-v3       # use a different model size
"""

import argparse
import os
import sys
import time
from pathlib import Path

from faster_whisper import WhisperModel


# ── Model sizes (speed vs accuracy tradeoff) ──────────────────────────────────
# tiny, base, small, medium, large-v1, large-v2, large-v3
# Use "large-v3" for best accuracy; "small" or "base" for fast/low-memory runs.
DEFAULT_MODEL = "base"
DEFAULT_DEVICE = "cpu"          # "cuda" if you have an Nvidia GPU
DEFAULT_COMPUTE_TYPE = "int8"   # "float16" for GPU; "int8" or "float32" for CPU


def transcribe_file(
    model: WhisperModel,
    audio_path: Path,
    language: str | None = None,
    output_dir: Path | None = None,
) -> Path:
    """
    Transcribe a single audio file and write a .txt sidecar next to it
    (or into output_dir if specified).

    Returns the path of the written transcript file.
    """
    print(f"\n▶  Transcribing: {audio_path.name}")
    t0 = time.perf_counter()

    segments, info = model.transcribe(
        str(audio_path),
        language=language,           # None → auto-detect
        beam_size=5,
        vad_filter=True,             # skip silent regions automatically
        vad_parameters=dict(
            min_silence_duration_ms=500,
        ),
    )

    print(
        f"   Detected language: {info.language!r} "
        f"(probability {info.language_probability:.0%})"
    )

    # Collect all segment text
    lines: list[str] = []
    for seg in segments:
        timestamp = f"[{_fmt_time(seg.start)} → {_fmt_time(seg.end)}]"
        lines.append(f"{timestamp}  {seg.text.strip()}")
        print(f"   {timestamp}  {seg.text.strip()}")

    # Write transcript
    dest_dir = output_dir or audio_path.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / (audio_path.stem + ".txt")
    out_path.write_text("\n".join(lines), encoding="utf-8")

    elapsed = time.perf_counter() - t0
    print(f"   ✓  Saved → {out_path}  ({elapsed:.1f}s)")
    return out_path


def _fmt_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch-transcribe MP3 files with faster-whisper.")
    parser.add_argument(
        "files",
        nargs="*",
        help="MP3 files to transcribe. Defaults to all *.mp3 in the current directory.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Whisper model size (default: {DEFAULT_MODEL}). "
             "Options: tiny, base, small, medium, large-v2, large-v3",
    )
    parser.add_argument(
        "--device",
        default=DEFAULT_DEVICE,
        help=f'Compute device (default: {DEFAULT_DEVICE}). Use "cuda" for GPU.',
    )
    parser.add_argument(
        "--compute-type",
        default=DEFAULT_COMPUTE_TYPE,
        help=f"Quantization type (default: {DEFAULT_COMPUTE_TYPE}). "
             'Use "float16" on GPU for speed.',
    )
    parser.add_argument(
        "--language",
        default=None,
        help='Force a specific language code, e.g. "en", "fr". Default: auto-detect.',
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory to write transcripts. Default: same folder as each audio file.",
    )
    args = parser.parse_args()

    # Resolve input files
    if args.files:
        audio_files = [Path(f) for f in args.files]
    else:
        audio_files = sorted(Path(".").glob("*.mp3"))

    if not audio_files:
        print("No MP3 files found. Pass file paths or run from a directory containing *.mp3 files.")
        sys.exit(1)

    missing = [f for f in audio_files if not f.exists()]
    if missing:
        for f in missing:
            print(f"Error: file not found — {f}", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output_dir) if args.output_dir else None

    # Load model once (downloads on first use, cached in ~/.cache/huggingface)
    print(f"Loading model '{args.model}' on {args.device} ({args.compute_type}) …")
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    print(f"Model loaded. Processing {len(audio_files)} file(s).\n")

    results: list[tuple[Path, Path]] = []
    errors: list[tuple[Path, str]] = []

    for audio_path in audio_files:
        try:
            out = transcribe_file(
                model,
                audio_path,
                language=args.language,
                output_dir=output_dir,
            )
            results.append((audio_path, out))
        except Exception as exc:
            print(f"   ✗  Failed: {exc}", file=sys.stderr)
            errors.append((audio_path, str(exc)))

    # Summary
    print(f"\n{'─' * 50}")
    print(f"Done — {len(results)} succeeded, {len(errors)} failed.")
    if errors:
        for f, msg in errors:
            print(f"  FAILED {f.name}: {msg}", file=sys.stderr)


if __name__ == "__main__":
    main()
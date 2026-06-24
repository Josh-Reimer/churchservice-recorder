#!/usr/bin/env python3
"""
Download the Whisper large-v3 model to ./models directory.
Run this once before starting the containers with: python download_model.py
"""
import os
import sys

def download_model():
    """Download Whisper large-v3 model to ./models/large-v3.pt"""
    try:
        import whisper
    except ImportError:
        print("ERROR: whisper not installed. Run: pip install openai-whisper")
        sys.exit(1)

    models_dir = "./models"
    os.makedirs(models_dir, exist_ok=True)

    model_path = os.path.join(models_dir, "large-v3.pt")
    if os.path.exists(model_path):
        print(f"✓ Model already exists at {model_path}")
        print(f"  Size: {os.path.getsize(model_path) / 1e9:.1f} GB")
        return

    print("Downloading Whisper large-v3 model...")
    print("This is a one-time operation (~2.9 GB, may take 5-10 minutes).\n")

    try:
        # download_root tells whisper where to save the model
        model = whisper.load_model("large-v3", download_root=models_dir)
        print(f"\n✓ Model downloaded successfully to {models_dir}/large-v3.pt")
        print(f"  Size: {os.path.getsize(model_path) / 1e9:.1f} GB")
        print("\nYou can now run: docker compose up -d --build")
    except Exception as e:
        print(f"✗ Failed to download model: {e}")
        sys.exit(1)

if __name__ == "__main__":
    download_model()

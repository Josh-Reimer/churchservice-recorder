import whisper

model = whisper.load_model("large")  # Change to "tiny", "small", "medium", or "large" as needed
result = model.transcribe("recording.mp3")
print(result["text"])

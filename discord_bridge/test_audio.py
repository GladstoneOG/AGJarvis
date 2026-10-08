import os
import time
import wave
import io
from dotenv import load_dotenv
from google import genai
from google.genai import types

from pathlib import Path
load_dotenv(Path(__file__).parent / ".env")
key = os.getenv("GEMINI_API_KEY")

print("Initializing Gemini client...")
client = genai.Client(api_key=key)

# Generate a small 1-second WAV
buf = io.BytesIO()
with wave.open(buf, 'wb') as wav:
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(16000)
    wav.writeframes(b'\x00\x00' * 16000)
wav_bytes = buf.getvalue()
print(f"Generated test audio: {len(wav_bytes)} bytes")

# Test 1: Upload via Files API
print("Testing client.files.upload...")
t0 = time.time()
try:
    audio_file = client.files.upload(
        file=io.BytesIO(wav_bytes),
        mime_type="audio/wav"
    )
    print(f"Uploaded to Files API: {audio_file.name} in {round(time.time() - t0, 2)}s")

    t1 = time.time()
    res = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=[audio_file, "Transcribe this audio. If silence, say 'SILENCE'."]
    )
    print(f"GenerateContent from Files API took {round(time.time() - t1, 2)}s: {repr(res.text)}")
    client.files.delete(name=audio_file.name)
except Exception as e:
    print(f"Files API test failed: {e}")

# Test 2: In-line Part.from_bytes
print("\nTesting types.Part.from_bytes...")
t2 = time.time()
try:
    part = types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav")
    res2 = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=[part, "Transcribe this audio. If silence, say 'SILENCE'."]
    )
    print(f"GenerateContent from bytes took {round(time.time() - t2, 2)}s: {repr(res2.text)}")
except Exception as e:
    print(f"Part.from_bytes failed: {e}")

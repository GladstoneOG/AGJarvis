import os, sys, time
from dotenv import load_dotenv

from pathlib import Path
load_dotenv(Path(__file__).parent / ".env")
key = os.getenv("GEMINI_API_KEY")

print("1. Importing genai...", flush=True)
from google import genai
from google.genai import types

print("2. Creating client...", flush=True)
client = genai.Client(api_key=key)

print("3. Testing text generation...", flush=True)
t0 = time.time()
res = client.models.generate_content(model="gemini-3.8-flash", contents="Hello")
print(f"3. Text response in {round(time.time() - t0, 2)}s: {res.text}", flush=True)

import logging
import socket
import time
from typing import Optional, Tuple

# DNS Optimization: Ensure responsive Google endpoints are prioritized to eliminate 10-15s ISP connect hangs.
# Must be patched BEFORE importing google.genai or initializing HTTP connection pools.
_ORIG_GETADDRINFO = socket.getaddrinfo
_RESPONSIVE_IPS = {"172.217.119.4", "172.217.116.4", "172.217.114.4", "172.217.118.4"}

def _patched_getaddrinfo(host, port, *args, **kwargs):
    res = _ORIG_GETADDRINFO(host, port, *args, **kwargs)
    if host == "generativelanguage.googleapis.com":
        try:
            responsive = [r for r in res if r[4][0] in _RESPONSIVE_IPS]
            if responsive:
                return responsive
        except Exception:
            pass
    return res

socket.getaddrinfo = _patched_getaddrinfo

from google import genai
from google.genai import types
from config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

def transcribe_audio_bytes(audio_bytes: bytes, content_type: Optional[str] = None, api_key: Optional[str] = None) -> Tuple[str, str]:
    """
    Transcribes audio bytes using the Gemini Multimodal API.
    Uses candidate models in order of latency and availability:
    gemini-3.5-flash-lite -> gemini-3.5-flash -> gemini-flash-lite-latest -> gemini-3.8-flash.
    Returns (transcription_text, model_name).
    """
    key = api_key or GEMINI_API_KEY
    if not key:
        raise ValueError("GEMINI_API_KEY is not configured. Please set it in .env.")

    # Clean mime type (Discord sends 'audio/ogg; codecs=opus')
    mime_type = "audio/ogg"
    if content_type:
        mime_type = content_type.split(";")[0].strip()

    # Fast-fail configuration: 15s timeout and 1 attempt per model so 503 errors fail over immediately
    client = genai.Client(
        api_key=key,
        http_options=types.HttpOptions(
            timeout=15000,
            retry_options=types.HttpRetryOptions(attempts=1)
        )
    )

    prompt = (
        "You are an accurate speech-to-text transcriber for a developer dictating commands to an AI coding assistant. "
        "Transcribe the audio verbatim. "
        "Ensure technical terms, file paths, code keywords, and function names are transcribed faithfully. "
        "Do NOT summarize, answer, or add any introductory or concluding notes. "
        "Output ONLY the exact transcription."
    )

    candidate_models = [
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash",
        "gemini-flash-lite-latest",
        "gemini-3.8-flash",
    ]
    last_error = None

    for model_name in candidate_models:
        t0 = time.time()
        try:
            logger.info(f"Attempting voice transcription with {model_name} ({len(audio_bytes)} bytes, mime={mime_type})...")
            response = client.models.generate_content(
                model=model_name,
                contents=[
                    types.Part.from_bytes(
                        data=audio_bytes,
                        mime_type=mime_type,
                    ),
                    prompt
                ]
            )
            elapsed = round(time.time() - t0, 2)
            if response:
                text = (response.text or "").strip()
                logger.info(f"Transcription succeeded with {model_name} in {elapsed}s: '{text[:80]}'")
                return text, model_name
        except Exception as e:
            elapsed = round(time.time() - t0, 2)
            logger.warning(f"Transcription failed on {model_name} after {elapsed}s: {e}")
            last_error = e

    raise RuntimeError(f"Transcription failed on active models ({candidate_models}): {last_error}")


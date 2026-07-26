"""
Configuration Module
Manages application settings, now consolidated for Gemini services.
"""

import os

class Config:
    """Application configuration"""

    # Gemini API Configuration
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-pro")
    GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-pro-vision") # Hypothetical model
    GEMINI_TTS_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-pro-tts") # Hypothetical model

    # Caching
    USE_CACHE = os.getenv("USE_CACHE", "true").lower() == "true"
    REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
    CACHE_TTL = int(os.getenv("CACHE_TTL", 86400))  # 24 hours

    # File handling
    MAX_UPLOAD_SIZE = int(os.getenv("MAX_UPLOAD_SIZE", 10485760))  # 10MB
    UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
    OUTPUT_DIR = os.getenv("OUTPUT_DIR", "outputs")

    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    DEBUG = os.getenv("DEBUG", "false").lower() == "true"

    # TTS Configuration
    TTS_API_KEY = os.getenv("TTS_API_KEY", "")
    TTS_API_URL = os.getenv("TTS_API_URL", "https://tts.ign3el.com/v1/audio/speech")
    KOKORO_URL = os.getenv("KOKORO_URL", "http://kokoro-tts:8880")
    PIPER_URL = os.getenv("PIPER_URL", "http://piper-tts:5000")

    # Wire format asked of Kokoro's OpenAI-compatible /v1/audio/speech.
    # mp3 is ~66% smaller than wav for identical input (measured 2026-07-26:
    # 133,974 B -> 45,740 B) and Kokoro encodes it server-side, so this costs us
    # no local CPU - which is the whole point on a box that is CPU-bound.
    # An env knob rather than a constant: a future GPU/RunPod backend may prefer
    # a different container, and no limit here should assume today's infra.
    TTS_AUDIO_FORMAT = os.getenv("TTS_AUDIO_FORMAT", "mp3")

    # "cpu" (default, self-hosted Kokoro container) or "runpod" (RunPod Flash
    # GPU endpoint, services/runpod_kokoro_client.py). Flip after the endpoint
    # has been tested end-to-end - see RUNPOD_ENDPOINT_ID_KOKORO in .env.
    TTS_BACKEND = os.getenv("TTS_BACKEND", "cpu").lower()

    # If the RunPod Kokoro call fails (bad endpoint, exhausted retries within
    # the call, timeout), fall back to the CPU container for that request
    # rather than let the scene fail outright. Disable to fail loud instead,
    # e.g. while actively debugging the RunPod path.
    TTS_RUNPOD_FALLBACK_TO_CPU = os.getenv("TTS_RUNPOD_FALLBACK_TO_CPU", "true").lower() == "true"


    @classmethod
    def get_info(cls) -> dict:
        """Get current configuration info"""
        return {
            "ai_provider": "Google Gemini",
            "text_generation": {
                "model": cls.GEMINI_TEXT_MODEL,
            },
            "image_generation": {
                "model": cls.GEMINI_IMAGE_MODEL,
            },
            "voice_generation": {
                "model": cls.GEMINI_TTS_MODEL,
            },
            "caching": {
                "enabled": cls.USE_CACHE,
                "redis_url": cls.REDIS_URL.split('@')[-1] if '@' in cls.REDIS_URL else cls.REDIS_URL,
                "ttl_seconds": cls.CACHE_TTL
            }
        }

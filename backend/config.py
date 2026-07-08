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
                "redis_url": cls.REDIS_URL,
                "ttl_seconds": cls.CACHE_TTL
            }
        }
"""
Configuration Module
Manages application settings, now consolidated for Gemini services.
"""

import os


def _env_int(name: str, default: int) -> int:
    """Read an integer setting, tolerating a trailing inline comment.

    `docker run --env-file` passes a line like `CACHE_TTL=86400  # 24 hours`
    through VERBATIM - unlike docker-compose, it does not strip inline
    comments. The value then arrives as the string "86400  # 24 hours" and
    int() raises at import time, taking the whole app down before it serves a
    request. Production never hit this only because compose passes an explicit
    environment list that happens to omit these keys; anything else reading the
    same .env (a test runner, a one-off `docker run`, a migration script) hits
    it immediately.

    A malformed value falls back to the default rather than raising: a wrong
    cache TTL is a bad setting, a crash at import is an outage.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    cleaned = raw.split("#", 1)[0].strip()
    try:
        return int(cleaned)
    except ValueError:
        return default


class Config:
    """Application configuration"""

    # Gemini API Configuration
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

    # Model names for the two Gemini jobs (see services/story_service.py for
    # why they're kept distinct - separate per-model RPD quotas). Env-driven
    # so a model swap is a config change, not a redeploy.
    LLM_STORY_MODEL = os.getenv("LLM_STORY_MODEL", "gemini-3.5-flash-lite")
    LLM_VISION_MODEL = os.getenv("LLM_VISION_MODEL", "gemini-3.1-flash-lite")

    # Groq is the fallback/alternate text provider (see LLM_BACKEND below).
    GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

    # "gemini" (default) or "groq" - which provider is tried FIRST for story
    # generation. The other still runs as the fallback either way, so this
    # never removes the safety net, only changes which one is primary.
    #
    # WARNING: Groq's free on_demand tier caps this account at 8000
    # tokens/minute total (prompt + completion), which silently truncates
    # large documents before the model ever sees them - measured to discard
    # the back half of a real NCERT chemistry chapter (see the 2026-08-03
    # comment above _STORY_MODEL in story_service.py). Only set this to
    # "groq" if your documents are short or you've moved to a paid Groq tier.
    LLM_BACKEND = os.getenv("LLM_BACKEND", "gemini").lower()

    # Caching
    USE_CACHE = os.getenv("USE_CACHE", "true").lower() == "true"
    REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
    CACHE_TTL = _env_int("CACHE_TTL", 86400)  # 24 hours

    # File handling
    MAX_UPLOAD_SIZE = _env_int("MAX_UPLOAD_SIZE", 10485760)  # 10MB
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

    # "kokoro" (default) or "chatterbox" (Chatterbox Multilingual, MIT
    # licensed - chosen over XTTS-v2 specifically to avoid XTTS's CPML
    # commercial-use license risk). Governs the synthesis engine for every
    # voice, including ar_teacher (Nour) - under "chatterbox" Arabic is
    # cloned from Piper's own current output rather than staying on Piper's
    # dedicated Arabic model; see services/kokoro_client.py::generate_tts_async,
    # the single place voice routing is decided. The 4 Hindi voices (hf_alpha/hf_beta/hm_omega/
    # hm_psi) only exist under "chatterbox" - they have no Kokoro equivalent,
    # so they stay non-functional under the "kokoro" default exactly as they
    # already were before this flag existed. Flip to "kokoro" for an instant
    # rollback if Chatterbox misbehaves - no redeploy needed, Kokoro's
    # worker code is kept (undeployed) in runpod-kokoro-test/kokoro_worker.py
    # and can be redeployed if ever needed.
    TTS_ENGINE = os.getenv("TTS_ENGINE", "kokoro").lower()

    # voice_id -> set of user_ids allowed to use it. Admins are always
    # implicitly allowed regardless of this list (see
    # services/kokoro_client.py::is_voice_allowed). Private voices are cloned
    # from a real person's reference audio (see runpod-kokoro-test/voice_refs)
    # and are deliberately NOT added to the public TeacherCard.jsx catalog -
    # this dict is the enforcement point that keeps them off-limits even to a
    # caller who guesses the voice_id directly. Extend by adding an id here
    # and redeploying the backend - no DB table needed for a single-digit
    # allowlist.
    PRIVATE_VOICES: dict[str, set[int]] = {
        "saba": {520},
    }

    # "flux-dev" (default, RUNPOD_ENDPOINT_ID_FLUX, 20/15 steps) or
    # "flux-schnell" (RUNPOD_ENDPOINT_ID_FLUX_SCHNELL, 4 steps, ~4-5x cheaper
    # per image - measured 2026-08-04). Different endpoint AND different
    # ComfyUI node graph (UNETLoader/DualCLIPLoader/VAELoader instead of
    # CheckpointLoaderSimple, since the two checkpoints ship in different
    # formats on the public runpod/worker-comfyui images). Flip only after
    # visually comparing output on real story prompts - schnell's own
    # img2img/reference-image behavior at low step counts is unverified.
    IMAGE_BACKEND = os.getenv("IMAGE_BACKEND", "flux-dev").lower()


    @classmethod
    def get_info(cls) -> dict:
        """Get current configuration info. Reflects the ACTUAL active provider
        per subsystem, not a fixed "Gemini does everything" assumption - each
        one is independently switchable, see LLM_BACKEND/IMAGE_BACKEND/TTS_BACKEND."""
        return {
            "text_generation": {
                "backend": cls.LLM_BACKEND,
                "story_model": cls.LLM_STORY_MODEL,
                "vision_model": cls.LLM_VISION_MODEL,
                "groq_model": cls.GROQ_MODEL,
            },
            "image_generation": {
                "backend": cls.IMAGE_BACKEND,
            },
            "voice_generation": {
                "backend": cls.TTS_BACKEND,
            },
            "caching": {
                "enabled": cls.USE_CACHE,
                "redis_url": cls.REDIS_URL.split('@')[-1] if '@' in cls.REDIS_URL else cls.REDIS_URL,
                "ttl_seconds": cls.CACHE_TTL
            }
        }

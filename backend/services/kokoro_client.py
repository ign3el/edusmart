import os
import requests
import logging

logger = logging.getLogger(__name__)

def generate_tts(text: str, voice: str = "af_sarah", speed: float = 1.0) -> bytes:
    """
    Generates TTS audio, routing to either the self-hosted CPU Kokoro
    container or the RunPod Flash GPU endpoint depending on Config.TTS_BACKEND.
    All three existing call sites (routers/admin.py, services/story_service.py,
    routers/upload.py) get whichever backend is configured with no changes on
    their end - this is the one place that decision is made.

    Args:
        text (str): The text to convert to speech.
        voice (str): The voice ID to use (default: "af_sarah").
        speed (float): The speed of speech (default: 1.0).

    Returns:
        bytes: The raw audio data (format depends on Config.TTS_AUDIO_FORMAT /
        RunPod's own encoder - callers must not assume WAV).

    Raises:
        Exception: If the TTS service fails or returns an error.
    """
    from config import Config

    if Config.TTS_BACKEND == "runpod":
        from services.runpod_kokoro_client import generate_tts as generate_tts_runpod, RunpodTTSError
        try:
            return generate_tts_runpod(text, voice=voice, speed=speed)
        except RunpodTTSError as e:
            if not Config.TTS_RUNPOD_FALLBACK_TO_CPU:
                logger.error(f"RunPod Kokoro failed, fallback disabled: {e}")
                raise Exception(f"RunPod TTS Service Failed: {e}")
            logger.warning(f"RunPod Kokoro failed, falling back to CPU Kokoro: {e}")
            # falls through to the CPU path below

    endpoint = f"{Config.KOKORO_URL}/v1/audio/speech"

    payload = {
        "model": "kokoro",
        "input": text,
        "voice": voice,
        # Was hardcoded "wav" while the caller saved the result as `scene_N.mp3`.
        # That mismatch is why 80 files on disk are RIFF bytes in an .mp3 name.
        "response_format": Config.TTS_AUDIO_FORMAT,
        "speed": speed
    }

    headers = {
        "Content-Type": "application/json"
    }

    try:
        # Increase timeout as TTS generation can take time
        response = requests.post(endpoint, json=payload, headers=headers, timeout=90)

        if response.status_code == 200:
            return response.content
        else:
            error_msg = f"Kokoro TTS Error {response.status_code}: {response.text}"
            logger.error(error_msg)
            raise Exception(error_msg)

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to connect to Kokoro TTS service at {endpoint}: {e}")
        raise Exception(f"TTS Service Connection Failed: {str(e)}")

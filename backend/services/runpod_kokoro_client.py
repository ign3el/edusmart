"""
RunPod Flash Kokoro TTS client.

Mirrors services/kokoro_client.py's generate_tts() signature/return type so it
can be dropped into that function's RunPod branch with no changes needed at
any of its three call sites (routers/admin.py, services/story_service.py,
routers/upload.py).

Follows the same /run -> poll /status/{id} pattern as the RunPod FLUX image
client in story_service.py, and the same RUNPOD_KEY / RUNPOD_ENDPOINT_ID_*
env-var convention (read directly via os.getenv, not through Config - that's
how every other RunPod integration in this codebase does it).
"""
import base64
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)


class RunpodTTSError(Exception):
    """Raised when the RunPod Kokoro endpoint fails or times out."""
    pass


def _sniff_audio_bytes(raw: bytes) -> str:
    """Identify audio format from magic bytes, never from a caller-supplied label.

    The RunPod worker's own 'format' field has been wrong before (it claimed
    mp3 while shipping RIFF/wav bytes, during testing on 2026-07-26) - so this
    is the one thing in this function that must not trust that field.
    """
    if len(raw) < 4:
        return ""
    if raw[:4] == b"RIFF":
        return "wav"
    if raw[:3] == b"ID3" or (raw[0] == 0xFF and (raw[1] & 0xE0) == 0xE0):
        return "mp3"
    return ""


def generate_tts(text: str, voice: str = "af_sarah", speed: float = 1.0) -> bytes:
    """
    Generate TTS audio via the RunPod Flash Kokoro endpoint (GPU).

    Args:
        text: The text to convert to speech.
        voice: The voice ID to use (default: "af_sarah").
        speed: The speed of speech (default: 1.0).

    Returns:
        bytes: Raw audio data. Format varies (mp3 expected) - callers that
        care about the format must sniff it themselves; this function does
        not relabel it.

    Raises:
        RunpodTTSError: On missing config, HTTP failure, job failure, or timeout.
    """
    endpoint_id = os.getenv("RUNPOD_ENDPOINT_ID_KOKORO")
    api_key = os.getenv("RUNPOD_KEY")
    if not endpoint_id or not api_key:
        raise RunpodTTSError("RUNPOD_ENDPOINT_ID_KOKORO or RUNPOD_KEY not set")

    timeout_s = int(os.getenv("RUNPOD_TTS_TIMEOUT_S", "180"))
    base_url = f"https://api.runpod.ai/v2/{endpoint_id}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"input": {"text": text, "voice": voice, "speed": speed}}

    start = time.time()
    try:
        resp = requests.post(f"{base_url}/run", headers=headers, json=payload, timeout=30)
    except requests.exceptions.RequestException as e:
        raise RunpodTTSError(f"Could not reach RunPod Kokoro endpoint: {e}")

    if resp.status_code != 200:
        raise RunpodTTSError(f"RunPod Kokoro /run returned {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    output = data.get("output") if data.get("status") == "COMPLETED" else None
    job_id = data.get("id")

    # Cold start / queued jobs poll status; a run that completed synchronously
    # (already-warm worker, fast job) skips straight to the output above.
    if output is None:
        if not job_id:
            raise RunpodTTSError(f"RunPod Kokoro /run response missing id and output: {data}")

        status_url = f"{base_url}/status/{job_id}"
        # 2s poll interval: cold starts run 20-250s, so finer granularity buys
        # nothing but extra requests against the endpoint.
        while True:
            elapsed = time.time() - start
            if elapsed > timeout_s:
                raise RunpodTTSError(
                    f"RunPod Kokoro job {job_id} did not complete within {timeout_s}s"
                )
            time.sleep(2)
            try:
                s = requests.get(status_url, headers=headers, timeout=20)
            except requests.exceptions.RequestException as e:
                logger.warning(f"RunPod Kokoro status poll failed, retrying: {e}")
                continue
            if s.status_code != 200:
                continue
            sdata = s.json()
            status = sdata.get("status")
            if status == "COMPLETED":
                output = sdata.get("output")
                break
            if status in ("FAILED", "CANCELLED", "TIMED_OUT"):
                raise RunpodTTSError(f"RunPod Kokoro job {job_id} ended with status {status}: {sdata}")

    if not isinstance(output, dict) or "audio_base64" not in output:
        raise RunpodTTSError(f"RunPod Kokoro job {job_id} completed with no audio_base64: {output}")

    try:
        raw = base64.b64decode(output["audio_base64"])
    except Exception as e:
        raise RunpodTTSError(f"RunPod Kokoro returned undecodable audio_base64: {e}")

    fmt = _sniff_audio_bytes(raw)
    if not fmt:
        raise RunpodTTSError(f"RunPod Kokoro returned {len(raw)} bytes that are neither wav nor mp3")

    elapsed = time.time() - start
    logger.info(f"✓ RunPod Kokoro TTS generated: {len(raw)} bytes ({fmt}) in {elapsed:.1f}s")
    return raw

"""
RunPod Flash Chatterbox Multilingual v3 TTS client.

Mirrors services/runpod_kokoro_client.py's /run -> poll /status/{id} pattern
and generate_tts() signature/return type exactly, so services/kokoro_client.py
can dispatch to it the same way it already dispatches to the Kokoro RunPod
client. Same RUNPOD_KEY / RUNPOD_ENDPOINT_ID_* env-var convention (read
directly via os.getenv, not through Config - matches every other RunPod
integration in this codebase).

Covers the English voices that used to run on Kokoro plus the 4 Hindi voices
(hf_alpha, hf_beta, hm_omega, hm_psi) that TeacherCard.jsx has always listed
but that never had a working backend - see
runpod-kokoro-test/chatterbox_worker.py. Chatterbox (MIT license, Resemble
AI) was chosen over XTTS-v2 specifically to avoid XTTS's CPML commercial-use
license risk - see the earlier planning discussion for the full tradeoff.

This replaces an earlier, never-wired ChatterboxClient in this same file that
posted straight at a bare CHATTERBOX_URL/Kokoro-shaped payload and never
consulted Config - dead code from an abandoned attempt, not a working
integration. Nothing imported it (verified via grep before removal).
"""
import base64
import logging
import os
import time

import requests

from services import api_usage

logger = logging.getLogger(__name__)


class ChatterboxTTSError(Exception):
    """Raised when the RunPod Chatterbox endpoint fails or times out."""
    pass


def _sniff_audio_bytes(raw: bytes) -> str:
    """Identify audio format from magic bytes, never from a caller-supplied label.

    Same rationale as runpod_kokoro_client.py's identical helper: a worker's
    own 'format' field has been wrong before, so this must not trust it.
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
    Generate TTS audio via the RunPod Flash Chatterbox Multilingual v3
    endpoint (GPU).

    Args:
        text: The text to convert to speech.
        voice: The voice ID to use (default: "af_sarah"). Language is derived
            server-side from the voice ID (see chatterbox_worker.py's
            VOICE_LANGUAGE).
        speed: The speed of speech (default: 1.0). Chatterbox has no native
            speed parameter - the worker applies it via ffmpeg's atempo
            filter server-side.

    Returns:
        bytes: Raw audio data. Format varies (mp3 expected) - callers that
        care about the format must sniff it themselves; this function does
        not relabel it.

    Raises:
        ChatterboxTTSError: On missing config, HTTP failure, job failure, or timeout.
    """
    endpoint_id = os.getenv("RUNPOD_ENDPOINT_ID_CHATTERBOX")
    api_key = os.getenv("RUNPOD_KEY")
    if not endpoint_id or not api_key:
        raise ChatterboxTTSError("RUNPOD_ENDPOINT_ID_CHATTERBOX or RUNPOD_KEY not set")

    # Longer default than Kokoro's 180s: a bigger model with a slower cold
    # start (see chatterbox_worker.py's idle_timeout=60 comment), and
    # per-sentence chunked synthesis takes longer than Kokoro's pass.
    timeout_s = int(os.getenv("RUNPOD_TTS_TIMEOUT_S", "240"))
    base_url = f"https://api.runpod.ai/v2/{endpoint_id}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    # repetition_penalty explicitly overridden from the worker/library's own
    # default (2.0) - confirmed live (2026-08-10) that 2.0 causes runaway
    # generation on the cross-lingual-cloned Hindi voices (a short sentence
    # came back as 28s of garbled/English-mixed audio; 1.3 fixed it to a
    # correct ~3s). Applies to all voices for consistency, not just Hindi -
    # no regression observed on English/Arabic at 1.3 during testing.
    payload = {"input": {"text": text, "voice": voice, "speed": speed, "repetition_penalty": 1.3}}

    start = time.time()
    # /runsync holds the HTTP connection open until the job finishes (or
    # `wait` ms elapse), so a warm scene (6-17s exec) returns in ONE request
    # instead of /run + N x 2s status polls - which alone cost 1-2s of
    # pure polling latency per scene. If the wait expires (cold start) the
    # same response carries the job id and the loop below takes over.
    wait_ms = int(os.getenv("RUNPOD_RUNSYNC_WAIT_S", "90")) * 1000
    try:
        resp = requests.post(
            f"{base_url}/runsync?wait={wait_ms}", headers=headers, json=payload,
            timeout=wait_ms / 1000 + 15,
        )
    except requests.exceptions.RequestException as e:
        api_usage.record("runpod-tts", "chatterbox", "runpod", ok=False)
        raise ChatterboxTTSError(f"Could not reach RunPod Chatterbox endpoint: {e}")

    if resp.status_code != 200:
        api_usage.record("runpod-tts", "chatterbox", "runpod", ok=False)
        raise ChatterboxTTSError(f"RunPod Chatterbox /runsync returned {resp.status_code}: {resp.text[:200]}")
    api_usage.record("runpod-tts", "chatterbox", "runpod", ok=True)

    data = resp.json()
    output = data.get("output") if data.get("status") == "COMPLETED" else None
    job_id = data.get("id")

    if output is None:
        if not job_id:
            raise ChatterboxTTSError(f"RunPod Chatterbox /run response missing id and output: {data}")

        status_url = f"{base_url}/status/{job_id}"
        while True:
            elapsed = time.time() - start
            if elapsed > timeout_s:
                raise ChatterboxTTSError(
                    f"RunPod Chatterbox job {job_id} did not complete within {timeout_s}s"
                )
            time.sleep(1)
            try:
                s = requests.get(status_url, headers=headers, timeout=20)
            except requests.exceptions.RequestException as e:
                logger.warning(f"RunPod Chatterbox status poll failed, retrying: {e}")
                continue
            if s.status_code != 200:
                continue
            sdata = s.json()
            status = sdata.get("status")
            if status == "COMPLETED":
                output = sdata.get("output")
                break
            if status in ("FAILED", "CANCELLED", "TIMED_OUT"):
                raise ChatterboxTTSError(f"RunPod Chatterbox job {job_id} ended with status {status}: {sdata}")

    if not isinstance(output, dict) or "audio_base64" not in output:
        raise ChatterboxTTSError(f"RunPod Chatterbox job {job_id} completed with no audio_base64: {output}")

    try:
        raw = base64.b64decode(output["audio_base64"])
    except Exception as e:
        raise ChatterboxTTSError(f"RunPod Chatterbox returned undecodable audio_base64: {e}")

    fmt = _sniff_audio_bytes(raw)
    if not fmt:
        raise ChatterboxTTSError(f"RunPod Chatterbox returned {len(raw)} bytes that are neither wav nor mp3")

    elapsed = time.time() - start
    # v10 workers report how many sentences the in-worker truncation guard
    # had to re-run; older workers don't send the key. Grep the logs for
    # "regen=" to see the real per-voice truncation rate.
    regen = output.get("regenerated_sentences")
    regen_note = f", regen={regen}" if regen is not None else ""
    logger.info(f"✓ RunPod Chatterbox TTS generated: {len(raw)} bytes ({fmt}) in {elapsed:.1f}s{regen_note}")
    return raw

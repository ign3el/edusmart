import asyncio
import os
import re
import subprocess
import tempfile
import requests
import logging

logger = logging.getLogger(__name__)

# Scene 0 narration split across this many concurrent Chatterbox jobs
# (generate_tts_async(parallel_chunks=...)). Chatterbox renders at ~1x real
# time on one worker (measured 2026-09-04: 21-41s per 25-37s scene), so a
# single scene-0 job is the longest pole between "text ready" and "first
# playable". Two halves on the endpoint's two workers finish in ~half the
# time; the MP3s are joined losslessly with ffmpeg's concat demuxer, which
# also rewrites the Xing/Info header so the browser sees one correct
# duration. Only worth it for the scene the user is waiting on - scenes
# 1..N already run in parallel with each other.
_SPLIT_MIN_CHARS = int(os.getenv("TTS_PARALLEL_MIN_CHARS", "160"))
_SENTENCE_END = re.compile(r"(?<=[.!?\u061f\u0964])\s+")


def _split_balanced(text: str, n: int) -> list[str]:
    """Split on sentence boundaries into up to n groups of similar length.
    Returns [text] when there is nothing worth splitting."""
    text = text.strip()
    sentences = [s for s in _SENTENCE_END.split(text) if s.strip()]
    if n <= 1 or len(sentences) < 2 or len(text) < _SPLIT_MIN_CHARS:
        return [text]
    n = min(n, len(sentences))
    target = len(text) / n
    groups: list[list[str]] = [[]]
    cur_len = 0
    for i, s in enumerate(sentences):
        remaining_groups = n - len(groups)
        # Midpoint rule: a sentence goes to the NEXT group when more than
        # half of it would overshoot this group's share. The old ">= share"
        # test only closed a group after it was already over, which handed
        # one long sentence to the wrong side ([519, 190] on a 4-sentence
        # scene) and made the "parallel" halves anything but.
        if (groups[-1] and remaining_groups > 0 and (len(sentences) - i) >= remaining_groups
                and cur_len + len(s) / 2 > target):
            groups.append([])
            cur_len = 0
        groups[-1].append(s)
        cur_len += len(s) + 1
    return [" ".join(g) for g in groups if g]


# Cold Chatterbox workers have been seen returning early-EOS audio: the MP3 is
# valid but covers only part of the text (measured 2026-09-04: 27.8 chars/s vs
# a normal 16-24 chars/s at speed 1.0). Anything faster than this many
# characters per second of audio (scaled by the requested speed) is treated as
# truncated and rendered once more; the longer result wins.
_MAX_CHARS_PER_S = float(os.getenv("TTS_MAX_CHARS_PER_S", "25"))


def _mp3_duration(data: bytes) -> float:
    """Seconds of audio in an MP3, via ffprobe. 0.0 when it cannot be read."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=True) as f:
        f.write(data)
        f.flush()
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f.name],
                capture_output=True, text=True, timeout=15,
            ).stdout.strip()
            return float(out) if out else 0.0
        except Exception:
            return 0.0


def _chatterbox_guarded(text: str, voice: str, speed: float) -> bytes:
    """generate_tts (Chatterbox) plus the truncation guard above."""
    from services.chatterbox_client import generate_tts as generate_tts_chatterbox
    audio = generate_tts_chatterbox(text=text, voice=voice, speed=speed)
    n_chars = len(text.strip())
    if n_chars < 40:
        return audio
    dur = _mp3_duration(audio)
    limit = _MAX_CHARS_PER_S * (speed or 1.0)
    if dur <= 0 or n_chars / dur <= limit:
        return audio
    logger.warning(
        f"⚠ Chatterbox audio looks truncated: {n_chars} chars in {dur:.1f}s "
        f"({n_chars / dur:.1f} chars/s > {limit:.0f}) - regenerating once"
    )
    retry = generate_tts_chatterbox(text=text, voice=voice, speed=speed)
    retry_dur = _mp3_duration(retry)
    logger.info(f"⚠ Chatterbox regenerated: {retry_dur:.1f}s vs {dur:.1f}s")
    return retry if retry_dur > dur else audio


def _concat_mp3(parts: list[bytes]) -> bytes:
    """Join MP3 parts with ffmpeg (-c copy). Raises on failure."""
    with tempfile.TemporaryDirectory(prefix="tts_join_") as d:
        names = []
        for i, b in enumerate(parts):
            fn = os.path.join(d, f"p{i}.mp3")
            with open(fn, "wb") as f:
                f.write(b)
            names.append(fn)
        lst = os.path.join(d, "list.txt")
        with open(lst, "w") as f:
            f.write("".join(f"file '{n}'\n" for n in names))
        out = os.path.join(d, "out.mp3")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out],
            check=True, capture_output=True, timeout=30,
        )
        with open(out, "rb") as f:
            return f.read()

# Voices that only exist under Chatterbox - no Kokoro equivalent, so they
# stay non-functional whenever Config.TTS_ENGINE == "kokoro" (unchanged from
# before this dispatcher existed; not a new gap).
_CHATTERBOX_ONLY_VOICES = {"hf_alpha", "hf_beta", "hm_omega", "hm_psi"}


def is_voice_allowed(voice: str, user: dict) -> bool:
    """Gate for voices cloned from a real person's audio (Config.PRIVATE_VOICES)
    - e.g. 'saba'. Every route that accepts a caller-supplied voice must call
    this before generating, not just hide the option in the UI: a client-side
    hide is not access control, the voice_id can be sent directly. Admins are
    always allowed; anyone else needs their user_id in the voice's allowlist.
    Public voices (not in PRIVATE_VOICES) are unaffected.
    """
    from config import Config

    if voice not in Config.PRIVATE_VOICES:
        return True
    if user.get("is_admin"):
        return True
    return user.get("id") in Config.PRIVATE_VOICES[voice]


async def generate_tts_async(text: str, voice: str = "af_sarah", speed: float = 1.0, parallel_chunks: int = 1) -> bytes:
    """
    Async entry point and the ONE place voice/engine routing is decided:
    Chatterbox Multilingual (RunPod) for everything when
    Config.TTS_ENGINE == "chatterbox" (including ar_teacher and the 4
    Chatterbox-only Hindi voices), Piper for ar_teacher + Kokoro for
    everything else when TTS_ENGINE == "kokoro" (the old behavior, kept as
    the rollback path - Piper is a dedicated Arabic model and was judged
    likely higher quality than a general multilingual model cloning it, so
    it's not simply deleted even though Chatterbox now covers Arabic too).

    Replaces three independently-duplicated `if voice == "ar_teacher"`
    branches that used to live in routers/admin.py, services/story_service.py,
    and routers/upload.py - that duplication is what caused a real bug once
    already (scene 0 in main.py used a different, unconfigured TTS client
    than scenes 1..N because its branch was hand-copied and drifted). All
    call sites should use this function instead of branching on voice
    themselves.

    Raises:
        Exception: If the selected TTS service fails or returns no audio.
    """
    from config import Config

    if voice in _CHATTERBOX_ONLY_VOICES or Config.TTS_ENGINE == "chatterbox":
        chunks = _split_balanced(text, parallel_chunks) if parallel_chunks > 1 else [text]
        if len(chunks) > 1:
            parts = await asyncio.gather(*(
                asyncio.to_thread(_chatterbox_guarded, text=c, voice=voice, speed=speed) for c in chunks
            ))
            if all(p[:3] == b"ID3" or p[:2] == b"\xff\xfb" or p[:2] == b"\xff\xf3" or p[:2] == b"\xff\xf2" for p in parts):
                try:
                    return await asyncio.to_thread(_concat_mp3, list(parts))
                except Exception as e:
                    logger.warning(f"TTS parallel join failed ({e}); regenerating as one job")
            else:
                logger.warning("TTS parallel chunks came back in mixed/non-mp3 formats; regenerating as one job")
        return await asyncio.to_thread(_chatterbox_guarded, text=text, voice=voice, speed=speed)

    if voice == "ar_teacher":
        from services.piper_client import piper_tts
        audio = await piper_tts.generate_audio(text, speed=speed, silence=0.3)
        if not audio:
            raise Exception("Piper TTS returned no audio")
        return audio

    return await asyncio.to_thread(generate_tts, text=text, voice=voice, speed=speed)


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

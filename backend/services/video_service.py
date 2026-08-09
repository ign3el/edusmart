"""ffmpeg-based rendering of a saved story into a single narrated video.

Each scene becomes one clip: its image, held for exactly the length of its
own narration audio, with that scene's text burned in as a caption. This
mirrors the pacing StoryPlayer.jsx already uses - one full-text caption block
per scene, shown for the scene's whole audio duration (see
StoryPlayer.jsx:384-418, 920-934) - rather than word-level karaoke timing,
because no word-level alignment data exists anywhere in the TTS pipeline.

Every ffmpeg/ffprobe invocation goes through asyncio.create_subprocess_exec
with an argument list (never shell=True, never string-interpolated), so
nothing here is a command-injection surface even though scene text is
attacker-influenced content - it only ever ends up inside a subtitle file
ffmpeg reads separately, never inside the command line itself.
"""
import asyncio
import logging
import os
import shutil
import tempfile
import textwrap
from pathlib import Path
from typing import Callable, Optional

from services import story_media
from story_storage import storage_manager

logger = logging.getLogger(__name__)

VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720
VIDEO_FPS = 30

# A Ken Burns pan/zoom (via ffmpeg's zoompan filter) was tried here and
# reverted (2026-08-09). It measured out as geometrically smooth and
# correctly-timed server-side (per-frame content genuinely differed, PTS was
# uniform CFR through every scene cut) across two rounds of fixes, but still
# read as "laggy/juddery, giving a headache" on the reporting user's actual
# phone both times. Rather than keep guessing at a third encoding tweak with
# no way to verify against real device playback, this reverts to the
# originally-shipped static-per-scene-image approach, which was never the
# subject of a complaint. If motion is revisited, validate against a real
# device screen recording before it ships, not just server-side metrics -
# those all passed here and the effect still wasn't watchable.

# One scene's ffmpeg encode (or the final concat) should finish in seconds to
# low tens of seconds on this hardware. Generous ceiling to catch a genuinely
# wedged process, not a slow-but-healthy one - see job_queue.py's
# JOB_TIMEOUT_SECONDS for the same reasoning applied to story generation.
FFMPEG_TIMEOUT_SECONDS = float(os.getenv("VIDEO_FFMPEG_TIMEOUT_SECONDS", "180"))

ProgressCallback = Optional[Callable[[int, int], "asyncio.Future"]]


class VideoRenderError(Exception):
    """Message is safe to store in story_videos.error and show the owner."""


def _wrap_caption(text: str) -> str:
    """Collapse to one line then re-wrap - libass does not auto-wrap long
    subtitle lines, so an unwrapped sentence would run off the frame."""
    text = " ".join((text or "").split())
    if not text:
        return " "
    lines = textwrap.wrap(text, width=42, break_long_words=False) or [text]
    return "\n".join(lines)


# A scene's narration is a whole paragraph. Burning it all in as ONE caption
# for the whole scene (the original approach) wraps into 6-8 lines and
# swallows almost the entire frame, leaving slivers of the image top and
# bottom - the opposite of "like subtitles". Real captions show a short
# phrase at a time. There's no word-level TTS alignment anywhere in this
# pipeline (see the module docstring), so exact per-word timing isn't
# possible - but splitting into fixed-size word chunks and spacing them
# across the scene's known audio duration, weighted by each chunk's word
# count, gets close enough that captions read like real subtitles instead
# of a wall of text.
CAPTION_CHUNK_WORDS = 12


def _split_caption_chunks(text: str) -> list:
    words = (text or "").split()
    if not words:
        return [" "]
    return [
        " ".join(words[i:i + CAPTION_CHUNK_WORDS])
        for i in range(0, len(words), CAPTION_CHUNK_WORDS)
    ]


def _srt_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    total_ms = int(round(seconds * 1000))
    hours, total_ms = divmod(total_ms, 3600000)
    minutes, total_ms = divmod(total_ms, 60000)
    secs, ms = divmod(total_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


async def _run_ffmpeg(args: list, cwd: Optional[Path] = None) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error", *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=FFMPEG_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise VideoRenderError(f"Rendering timed out after {FFMPEG_TIMEOUT_SECONDS:.0f}s.")
    if proc.returncode != 0:
        tail = stderr.decode("utf-8", "replace")[-2000:]
        logger.error(f"ffmpeg failed (exit {proc.returncode}): {tail}")
        raise VideoRenderError("Video rendering failed. Please try again.")


async def _probe_duration(path: Path) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise VideoRenderError(f"Could not read audio duration for {path.name}.")
    if proc.returncode != 0:
        logger.error(f"ffprobe failed on {path}: {stderr.decode('utf-8', 'replace')[-500:]}")
        raise VideoRenderError(f"Could not read audio duration for {path.name}.")
    try:
        return float(stdout.decode().strip())
    except ValueError:
        raise VideoRenderError(f"Could not read audio duration for {path.name}.")


def _scene_asset_path(
    story_dir: Path, raw_url: Optional[str], idx: int, fallback_ext: str
) -> Optional[Path]:
    """Same basename-only resolution share.py's _share_media_url uses - the
    stored URL may be any of three historical shapes, only the filename part
    matters, and story_media.resolve_scene_file tolerates both on-disk naming
    conventions."""
    if raw_url:
        filename = raw_url.split("?")[0].rstrip("/").split("/")[-1]
        if not filename or not story_media.is_safe_filename(filename):
            filename = f"scene_{idx}.{fallback_ext}"
    else:
        filename = f"scene_{idx}.{fallback_ext}"
    return story_media.resolve_scene_file(story_dir, filename)


async def render_story_video(
    story_id: str,
    story_data: dict,
    progress_cb: ProgressCallback = None,
) -> str:
    """Render `story_data`'s scenes into saved_stories/{story_id}/video.mp4.

    Returns the absolute output path. Raises VideoRenderError with a
    caller-safe message on any failure. The output is written to a temp file
    and moved into place only on success, so a failed re-render never
    clobbers a previously working video.mp4.
    """
    scenes = story_data.get("scenes") or []
    if not scenes:
        raise VideoRenderError("This story has no scenes to render.")

    story_dir = Path(storage_manager.get_story_path(story_id, in_saved=True)).resolve()
    if not story_dir.exists():
        raise VideoRenderError("Story files are missing.")

    work_dir = Path(tempfile.mkdtemp(prefix=f"video_{story_id}_"))
    clip_paths = []
    try:
        for idx, scene in enumerate(scenes):
            image_path = _scene_asset_path(story_dir, scene.get("image_url"), idx, "png")
            audio_path = _scene_asset_path(story_dir, scene.get("audio_url"), idx, "mp3")
            if not image_path or not audio_path:
                raise VideoRenderError(
                    f"Scene {idx + 1} isn't fully generated yet - "
                    "image and narration are both required before creating a video."
                )

            duration = await _probe_duration(audio_path)

            chunks = _split_caption_chunks(scene.get("text", ""))
            word_counts = [max(1, len(c.split())) for c in chunks]
            total_words = sum(word_counts)
            cue_blocks = []
            cursor = 0.0
            for cue_idx, (chunk, words) in enumerate(zip(chunks, word_counts), start=1):
                # Last cue absorbs any rounding remainder so cues always sum
                # to exactly `duration`, not slightly short of it.
                cue_end = cursor + (duration * words / total_words) if cue_idx < len(chunks) else duration
                cue_blocks.append(
                    f"{cue_idx}\n{_srt_timestamp(cursor)} --> {_srt_timestamp(cue_end)}\n"
                    f"{_wrap_caption(chunk)}\n"
                )
                cursor = cue_end

            srt_path = work_dir / f"scene_{idx}.srt"
            srt_path.write_text("\n".join(cue_blocks), encoding="utf-8")
            # The subtitles filter treats ':' as an option separator in its
            # path argument, on top of the usual filtergraph escaping rules.
            srt_arg = str(srt_path).replace("\\", "/").replace(":", "\\:")

            clip_path = work_dir / f"clip_{idx}.mp4"
            # BorderStyle=1 (outline + drop shadow) rather than 3 (opaque
            # box) - a solid BackColour box was covering a big chunk of the
            # image behind every caption. Outline+shadow keeps text readable
            # over any background without blocking the art.
            vf = (
                f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,"
                f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
                f"subtitles={srt_arg}:force_style='FontSize=26,PrimaryColour=&H00FFFFFF,"
                f"OutlineColour=&H00000000,BackColour=&H00000000,BorderStyle=1,"
                f"Outline=2,Shadow=1,MarginV=40'"
            )
            # -t <duration> rather than -shortest: with a -loop 1 still image
            # against a resampled/AAC-encoded audio track, -shortest's
            # stream-end detection overshoots (measured ~3x too long on short
            # clips) - an explicit duration, already known from the ffprobe
            # above, is exact instead of approximate.
            await _run_ffmpeg([
                "-loop", "1", "-i", str(image_path),
                "-i", str(audio_path),
                "-vf", vf,
                "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
                "-r", str(VIDEO_FPS),
                "-c:a", "aac", "-b:a", "192k",
                "-t", f"{duration:.3f}",
                str(clip_path),
            ])
            clip_paths.append(clip_path)
            if progress_cb:
                await progress_cb(idx + 1, len(scenes))

        concat_list = work_dir / "concat.txt"
        concat_list.write_text(
            "\n".join(f"file '{p.name}'" for p in clip_paths), encoding="utf-8"
        )
        tmp_output = work_dir / "output.mp4"
        # -c copy: every clip above was encoded with identical parameters by
        # this same function, so a lossless stream copy is safe and fast -
        # no need to re-encode a second time just to join them.
        await _run_ffmpeg([
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-c", "copy",
            str(tmp_output),
        ], cwd=work_dir)

        final_output = story_dir / "video.mp4"
        # shutil.move (not os.replace): work_dir is under the system temp
        # dir, story_dir is a bind-mounted volume - a cross-filesystem rename
        # raises EXDEV, so this needs the copy+delete fallback shutil provides.
        shutil.move(str(tmp_output), str(final_output))
        return str(final_output)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

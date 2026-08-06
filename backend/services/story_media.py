"""Locating and serving a story's scene files.

Story folders accumulated two naming conventions over time - `scene_0.png` and
`<uuid>_scene_0.png` - and the app happily stores either shape in `story_data`.
The lookup below therefore tries a waterfall of patterns rather than trusting
the recorded filename, which is what makes stories generated months apart all
still play.

This lived inline in `main.py`'s `/api/saved-stories/...` handler, with the
CORS header block copy-pasted once per branch. It moved here when share links
needed the identical lookup: two copies of a five-branch waterfall drift, and
the copy that drifts is the one nobody is watching.
"""
import glob
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Media served to <img>/<audio>/<video> tags on the site's own origin.
_CORS_ORIGIN = os.getenv("MEDIA_CORS_ORIGIN", "https://edusmart.ign3el.com")


def is_safe_filename(filename: str) -> bool:
    """Legitimate scene files are flat basenames - never nested, never absolute."""
    return not (".." in filename or filename.startswith("/") or "\\" in filename)


def sniff_media_type(path) -> str:
    """Media type from the file's leading bytes rather than its extension.

    Returns "" when the file cannot be read or the header is unrecognised, so
    callers can fall back to whatever they were going to do anyway.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
    except OSError:
        return ""
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav"
    # MP3 is either an ID3 tag or a raw frame sync (0xFF 0xEx/0xFx).
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "audio/mpeg"
    if head[:4] == b"OggS":
        return "audio/ogg"
    # ISO-BMFF (mp4/m4a): 'ftyp' box at offset 4. Distinguishing audio-only
    # from video would need the brand; every mp4 this app writes is a rendered
    # story video, so video/mp4 is the honest answer.
    if head[4:8] == b"ftyp":
        return "video/mp4"
    return ""


def resolve_scene_file(story_dir: Path, filename: str) -> Optional[Path]:
    """Find `filename` inside `story_dir`, tolerating both naming conventions.

    Returns None if nothing matches. Callers are responsible for having already
    authorised the request - this function performs no access control, only the
    path-traversal guard that keeps the search inside the folder.
    """
    if not is_safe_filename(filename):
        return None
    if not story_dir.exists():
        return None

    # Exact match.
    exact_path = story_dir / filename
    if exact_path.exists() and exact_path.is_file():
        return exact_path

    # Pattern 1: asked for the bare name, stored with a UUID prefix.
    matches = glob.glob(str(story_dir / f"*_{filename}"))
    if matches:
        return Path(matches[0])

    # Pattern 2: asked for a UUID-prefixed name, stored bare.
    if "_scene_" in filename:
        base_filename = filename.split("_scene_")[-1]
        old_format_path = story_dir / f"scene_{base_filename}"
        if old_format_path.exists() and old_format_path.is_file():
            return old_format_path

    # Pattern 3: asked for the bare name, stored with a prefix and same ext.
    if filename.startswith("scene_"):
        parts = filename.split("_")
        if len(parts) >= 2:
            scene_part = parts[1].split(".")[0]
            ext = filename.split(".")[-1]
            matches = glob.glob(str(story_dir / f"*_scene_{scene_part}.{ext}"))
            if matches:
                return Path(matches[0])

    # Pattern 4: last resort - anything carrying this scene number.
    if "scene_" in filename:
        scene_match = filename.split("scene_")[-1].split(".")[0]
        matches = glob.glob(str(story_dir / f"*scene_{scene_match}*"))
        if matches:
            return Path(matches[0])

    try:
        logger.error(
            f"❌ File not found: {filename}; available in {story_dir}: "
            f"{[f.name for f in story_dir.iterdir()]}"
        )
    except OSError:
        logger.error(f"❌ File not found: {filename} (and {story_dir} is unreadable)")
    return None


# Story media is revocable, per-viewer content: an owner can turn a share link
# off, delete a story, or have their token expire. A SHARED cache must therefore
# never hold it. This was not theoretical - Cloudflare cached a shared scene
# image under its default extension rules (`cf-cache-status: HIT`, `age: 778`)
# and kept serving it for four hours AFTER the link was revoked, while the
# origin correctly answered 404. `private` is what stops that: the visitor's own
# browser may still cache it for the length of a sitting, which is all the
# performance that actually matters here.
_MEDIA_CACHE_CONTROL = f"private, max-age={os.getenv('MEDIA_CACHE_MAX_AGE', '300')}"


def media_response(path, download_name: Optional[str] = None):
    """FileResponse with the CORS and cache headers media tags need here."""
    from fastapi.responses import FileResponse

    headers = {"Cache-Control": _MEDIA_CACHE_CONTROL}
    if download_name:
        headers["Content-Disposition"] = f'attachment; filename="{download_name}"'

    response = FileResponse(
        path,
        media_type=sniff_media_type(path) or None,
        headers=headers,
    )
    response.headers["Access-Control-Allow-Origin"] = _CORS_ORIGIN
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response

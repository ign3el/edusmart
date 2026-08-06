"""Public share links for saved stories.

A story is normally readable only by its owner. `is_public` widens that to
other *signed-in* users (discoverability); it does not create a URL a stranger
can open. This router adds that second, stronger thing: a random bearer token
that grants read-only, unauthenticated access to one story, revocable at any
time by its owner.

Why a separate token instead of reusing `is_public`:
  - Different consent. Ticking "let other users find this" is not the same as
    "publish this on the internet". Overloading the flag would retroactively
    expose every already-public story.
  - Revocability. Rotating a token dead-ends every copy of the old URL;
    is_public has no such handle.
  - Blast radius. A leaked token exposes exactly one story.

Everything under /api/share/{token} is UNAUTHENTICATED by design - that is the
whole point - so each handler treats the token as the sole credential and
strips owner identity from anything it returns.
"""
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from database_models import StoryOperations, User
from routers.auth import client_ip, get_current_user, _rate_limiter
from services import story_media
from story_storage import storage_manager

logger = logging.getLogger(__name__)

owner_router = APIRouter(tags=["Sharing"])
public_router = APIRouter(prefix="/api/share", tags=["Sharing - Public"])

# An unauthenticated endpoint that answers "does this token exist?" is a
# guessing surface. 256 bits of entropy makes guessing hopeless on its own, but
# throttling keeps a scanner from turning the route into free CPU and log noise.
SHARE_LOOKUP_MAX_ATTEMPTS = 60
SHARE_LOOKUP_WINDOW_SECONDS = 60

# A story folder holds more than the story: metadata.json records the source
# document's path, grade level and internal ids, and older folders still carry
# the raw extracted lesson text. The owner is welcome to all of it; a stranger
# holding a share link is not. So the public media route serves scene assets
# ONLY, by pattern - an allow-list, because a deny-list would have to be updated
# every time the generator starts writing a new kind of file.
_SHAREABLE_ASSET = re.compile(
    r"^(?:[A-Za-z0-9_\-]+_)?scene_\d+\.(?:png|jpg|jpeg|webp|gif|wav|mp3|ogg|m4a|mp4)$",
    re.IGNORECASE,
)

# Same resolution order as routers/billing.py:31 - the share URL has to point at
# the site a human will open, not at the API.
FRONTEND_URL = (
    os.getenv("FRONTEND_URL") or os.getenv("APP_URL") or "https://edusmart.ign3el.com"
).rstrip("/")


class ShareCreateRequest(BaseModel):
    rotate: bool = False


def _share_url(token: str) -> str:
    return f"{FRONTEND_URL}/s/{token}"


def _resolve_token_or_404(token: str, request: Request) -> dict:
    """Look up a share token, throttled per IP. 404 - never 403 - for a bad
    token: distinguishing "wrong" from "revoked" would confirm which tokens
    once existed."""
    ip = client_ip(request)
    if not _rate_limiter.check(
        f"share:{ip}",
        max_attempts=SHARE_LOOKUP_MAX_ATTEMPTS,
        window_seconds=SHARE_LOOKUP_WINDOW_SECONDS,
    ):
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please slow down.",
            headers={"Retry-After": str(SHARE_LOOKUP_WINDOW_SECONDS)},
        )

    story = StoryOperations.get_story_by_share_token(token)
    if not story:
        raise HTTPException(status_code=404, detail="This link is no longer available.")
    return story


def _public_story_payload(story: dict) -> dict:
    """Strip the row down to what an anonymous viewer may see.

    Built as an allow-list, not by deleting keys off the DB row: a column added
    later (owner email, internal notes, anything) must not leak by default just
    because nobody remembered to delete it here.
    """
    story_id = story["story_id"]
    token = story["share_token"]
    story_data = story.get("story_data") or {}

    scenes = []
    for idx, scene in enumerate(story_data.get("scenes", [])):
        scenes.append({
            "text": scene.get("text", ""),
            "image_url": _share_media_url(token, scene.get("image_url"), story_id, idx, "png"),
            "audio_url": _share_media_url(token, scene.get("audio_url"), story_id, idx, "mp3"),
        })

    return {
        "name": story.get("name"),
        "story_data": {
            "title": story_data.get("title") or story.get("name"),
            "scenes": scenes,
            # The quiz is the most convincing part of a demo, and it carries no
            # owner data - just questions and answers.
            "quiz": _normalise_quiz(story_data.get("quiz")),
            # Same reasoning as the quiz: revision notes are lesson content, not
            # owner data, and a shared story that stops one screen short of the
            # quiz would look broken rather than deliberately trimmed.
            "key_points": [
                p.strip() for p in (story_data.get("key_points") or [])
                if isinstance(p, str) and p.strip()
            ],
        },
    }


def _normalise_quiz(raw) -> list:
    """Always hand back a list.

    Stories persist `quiz` as a JSON *string* about as often as a list, and the
    authenticated path papers over it in the browser (App.jsx parses it before
    handing it to the player). The share payload is a public API contract, so it
    normalises here instead - otherwise the shared page renders a quiz by
    iterating over the characters of a string.
    """
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            logger.warning("Shared story has an unparseable quiz field; serving an empty quiz")
            return []
    return []


def _share_media_url(token: str, raw_url: Optional[str], story_id: str, idx: int, fallback_ext: str) -> str:
    """Rewrite a stored media URL to its /api/share/{token}/media/... equivalent.

    Stories carry three historical URL shapes (legacy /api/outputs/..., the
    current /api/saved-stories/<id>/..., and bare filenames). Only the basename
    matters here - story_media.resolve_scene_file does the rest - so every shape
    collapses to the same thing.
    """
    if not raw_url:
        return ""
    filename = raw_url.split("?")[0].rstrip("/").split("/")[-1]
    if not filename or not story_media.is_safe_filename(filename):
        filename = f"scene_{idx}.{fallback_ext}"
    return f"/api/share/{token}/media/{filename}"


# --- Owner-side: create, inspect, revoke -----------------------------------

@owner_router.get("/api/story/{story_id}/share")
async def get_share_state(story_id: str, user: User = Depends(get_current_user)):
    state = StoryOperations.get_share_token(story_id, user)
    if state is None:
        raise HTTPException(status_code=404, detail="Story not found or not yours.")
    token = state["share_token"]
    return {
        "shared": bool(token),
        "share_url": _share_url(token) if token else None,
        "share_created_at": state["share_created_at"],
    }


@owner_router.post("/api/story/{story_id}/share")
async def create_share(
    story_id: str,
    payload: ShareCreateRequest = ShareCreateRequest(),
    user: User = Depends(get_current_user),
):
    """Idempotent: pressing Share twice returns the same link, so a URL already
    pasted into a message keeps working. `rotate` is the leaked-link escape."""
    token = StoryOperations.create_share_token(story_id, user, rotate=payload.rotate)
    if not token:
        raise HTTPException(status_code=404, detail="Story not found or not yours.")
    logger.info(f"Share link {'rotated' if payload.rotate else 'issued'} for story {story_id} by user {user.get('id')}")
    return {"shared": True, "share_url": _share_url(token)}


@owner_router.delete("/api/story/{story_id}/share")
async def revoke_share(story_id: str, user: User = Depends(get_current_user)):
    if not StoryOperations.revoke_share_token(story_id, user):
        raise HTTPException(status_code=404, detail="Story not found or not yours.")
    logger.info(f"Share link revoked for story {story_id} by user {user.get('id')}")
    return {"shared": False}


# --- Public-side: no authentication anywhere below --------------------------

@public_router.get("/{token}")
async def get_shared_story(token: str, request: Request):
    """The shared story itself. No auth: the token is the credential."""
    story = _resolve_token_or_404(token, request)
    return _public_story_payload(story)


@public_router.api_route("/{token}/media/{filename:path}", methods=["GET", "HEAD"])
async def get_shared_media(token: str, filename: str, request: Request):
    """Scene image/audio for a shared story.

    Resolution goes through the same helper the authenticated media route uses,
    so a story that plays for its owner also plays for a visitor - no second
    filename-matching implementation to drift out of sync.
    """
    story = _resolve_token_or_404(token, request)

    if not story_media.is_safe_filename(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    # 404, not 403: whether a given non-scene file exists in the folder is not
    # something an anonymous caller should be able to probe for either.
    if not _SHAREABLE_ASSET.match(filename):
        raise HTTPException(status_code=404, detail="File not found")

    try:
        story_dir = Path(storage_manager.get_story_path(story["story_id"], in_saved=True))
    except ValueError:
        raise HTTPException(status_code=404, detail="File not found")

    resolved = story_media.resolve_scene_file(story_dir, filename)
    if resolved is None:
        raise HTTPException(status_code=404, detail="File not found")

    return story_media.media_response(resolved)

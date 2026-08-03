import os
import uuid
import asyncio
import time
import mimetypes
import io
import re
import json
import zipfile
import logging
import hashlib
import shutil
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException, Depends, Request, Body
from fastapi import BackgroundTasks as BackgroundTasksExplicit
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

# Import the new, clean refactored modules
from database import initialize_database, get_db_cursor
from routers.auth import router as auth_router, get_current_user
from routers.admin import router as admin_router
from routers.upload import router as upload_router
from routers.billing import router as billing_router, check_and_reserve_credit, refund_credit
from core.setup import create_admin_user
from database_models import User, StoryOperations, UserOperations
from auth import verify_token
from services.story_service import (
    StoryService,
    normalize_quiz_size,
    DEFAULT_QUIZ_SIZE,
    QUIZ_SIZE_OPTIONS,
)
from services.tts_service import kokoro_tts
from services.hash_service import hash_service
from job_state import job_manager
from story_storage import storage_manager, cleanup_scheduler_task, database_cleanup_scheduler_task
from services.concurrency import tts_governor, governor_snapshot, log_limits
from services import vision_budget
from services import failure_reasons
from services.job_queue import generation_queue, admit_generation
from services.grade_bands import resolve_grade_spec
from typing import Optional, TYPE_CHECKING, Dict, Any, List

# Type checking imports for Pylance
if TYPE_CHECKING:
    from services.story_service import StoryService

# Pause between generation attempt 1 and 2. Groq reserves prompt tokens plus
# requested max_tokens against a single per-minute budget, so an immediate retry
# arrives while attempt 1 still owns that window and 429s on contact - spending
# the one retry without ever reaching the model.
RETRY_COOLDOWN_SECONDS = 20

# Load environment variables from .env file
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- App Initialization ---
app = FastAPI(title="EduStory API")

# Add CORS middleware to allow requests from frontend domain.
# Configured for production with specific origins for better security.
# Deliberately hardcoded, not env-driven: a CORS_ORIGINS env var existed
# alongside this for a while but was never read here, and its value
# (localhost-only) would have broken prod CORS if it ever had been wired
# up. Removed from docker-compose.yml on 2026-07-26; change this list
# directly if the origin set needs to change.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://edusmart.ign3el.com",
        "http://localhost:3000",
        "http://127.0.0.1:3000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Background Tasks ---
# Note: Old cleanup system removed. Now using story_storage.py with 24-hour TTL cleanup

# --- Startup Event ---
@app.on_event("startup")
async def startup_event():
    """
    This function runs when the application starts.
    It initializes the database and starts background tasks.
    """
    # asyncio.to_thread() and loop.run_in_executor() share ONE default
    # executor, which Python sizes at min(32, cpu_count + 4) - seven threads
    # on this box. Every TTS call, Groq call and offloaded file write competes
    # for those slots, so throughput flat-lined at ~7 concurrent operations
    # regardless of how many users were connected. The threads sit blocked on
    # network I/O rather than burning CPU, so this should track the number of
    # in-flight requests we intend to serve, not the core count. Env-driven so
    # a bigger host only needs a variable change.
    from concurrent.futures import ThreadPoolExecutor
    _pool_workers = int(os.getenv("THREAD_POOL_WORKERS", "128"))
    _default_workers = min(32, (os.cpu_count() or 1) + 4)
    asyncio.get_running_loop().set_default_executor(
        ThreadPoolExecutor(
            max_workers=_pool_workers, thread_name_prefix="edusmart-io"
        )
    )
    logger.info(
        f"✓ Thread pool sized to {_pool_workers} workers "
        f"(Python default here would be {_default_workers})"
    )

    # Skip database operations in development mode
    if os.getenv("ENV") == "development":
        logger.info("✓ Development mode: Skipping database initialization")
        logger.info("✓ Development mode: Skipping admin user creation")
    else:
        try:
            logger.info("Initializing database...")
            initialize_database()
            logger.info("Database initialization successful.")
            
            logger.info("Performing initial user setup (admin check)...")
            create_admin_user()
            logger.info("Initial user setup complete.")
            
        except Exception as e:
            logger.critical(f"FATAL: Could not initialize database on startup. Error: {e}")
            # In a real app, you might want the app to fail fast if the DB is unavailable.
    
    logger.info("Initializing story storage manager...")
    # Storage manager auto-initializes on import

    # Generation runs through a durable queue, not FastAPI BackgroundTasks.
    # BackgroundTasks started every accepted upload immediately and in
    # unbounded parallel - 50 uploads meant 50 concurrent workflows, each with
    # its own RunPod and Kokoro fan-out - and nothing was written down, so a
    # restart silently dropped every in-flight job and left the story stuck at
    # "initializing" with the user's credit already spent.
    #
    # Queue recovery runs BEFORE the orphaned-story reconciler below and
    # refunds the jobs it abandons, because it marks those stories 'failed'.
    # Reverse the order and the reconciler would still see them as
    # 'processing' and refund the same credit a second time.
    logger.info("Starting generation queue...")
    try:
        generation_queue.set_handler(run_ai_workflow_progressive_mobile)
        recovered = await generation_queue.start()
        for job in recovered.get("abandoned_jobs", []):
            if job.get("user_id"):
                try:
                    refund_credit(job["user_id"], job["story_id"])
                except Exception as refund_err:
                    logger.warning(
                        f"Could not refund credit for abandoned job {job['story_id']}: {refund_err}"
                    )
        log_limits()
    except Exception as e:
        # Non-fatal on purpose, matching the database-init handling above, but
        # loud: with no workers running, uploads are accepted and never start.
        logger.critical(f"FATAL: generation queue failed to start: {e}")

    logger.info("Reconciling orphaned jobs from a previous process...")
    try:
        orphaned = job_manager.reconcile_orphaned_jobs()
        for job in orphaned:
            if job.get("user_id"):
                try:
                    refund_credit(job["user_id"], job["story_id"])
                except Exception as refund_err:
                    logger.warning(f"Could not refund credit for orphaned job {job['story_id']}: {refund_err}")
        if orphaned:
            logger.info(f"Marked {len(orphaned)} orphaned job(s) as failed and refunded credits where applicable.")
    except Exception as e:
        logger.warning(f"Orphaned job reconciliation failed: {e}")

    logger.info("Starting story cleanup scheduler (24-hour TTL)...")
    asyncio.create_task(cleanup_scheduler_task())
    
    # Skip database cleanup in development mode
    if os.getenv("ENV") != "development":
        logger.info("Starting database cleanup scheduler (runs every 2 days)...")
        asyncio.create_task(database_cleanup_scheduler_task())
    else:
        logger.info("✓ Development mode: Skipping database cleanup scheduler")


# --- API Routers ---
# Include the new authentication router, which contains /signup, /token, /me
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(upload_router)
app.include_router(billing_router)


# --- Non-Auth related application logic ---

# Type annotation for gemini service to help Pylance

# Explicitly declare the methods Pylance should recognize
# This helps with static analysis while maintaining runtime functionality
gemini: 'StoryService' = StoryService()
jobs: Dict[str, Any] = {}

# Method existence hints for Pylance (these don't affect runtime)
# Pylance will recognize these methods exist on the gemini instance
if False:
    # These are never executed but help Pylance understand the types
    _ = gemini.process_file_to_story
    _ = gemini.generate_image
    _ = gemini._extract_json_from_response
    _ = gemini._exponential_backoff
    _ = gemini._call_with_exponential_backoff

# Note: generated_stories and saved_stories directories created by storage_manager
# Keeping uploads folder for backward compatibility during migration
os.makedirs("uploads", exist_ok=True)

mimetypes.add_type('audio/mpeg', '.mp3')
mimetypes.add_type('image/png', '.png')

# --- Static File Serving ---
# NOTE: outputs/, generated_stories/ and saved_stories/ are intentionally NOT
# mounted as raw StaticFiles - a bare mount here would shadow the custom
# routes below (which enforce auth + per-story ownership) and serve every
# file in those directories to anyone, unauthenticated. The custom
# @app.api_route handlers below are the only way into these directories.
# app.mount("/api/outputs", StaticFiles(directory="outputs"), name="outputs")
# app.mount("/api/saved-stories", StaticFiles(directory="saved_stories"), name="saved_stories")
# app.mount("/api/generated-stories", StaticFiles(directory="generated_stories"), name="generated_stories")
#
# uploads/ was mounted at /api/uploads with no auth at all, but nothing in
# this codebase ever writes a file into it (the real upload flow writes into
# generated_stories/{story_id}/ instead) - it's dead weight, not backward
# compatibility, so it's simply not mounted at all rather than re-secured.


async def get_media_user(request: Request, token: Optional[str] = None) -> dict:
    """
    Auth dependency for story media (images/audio). Browsers can't attach a
    custom Authorization header to <img>/<audio> tags, so this accepts the
    JWT either as a Bearer header (fetch/XHR callers) or as a `token` query
    param (tag src= callers) - the same pattern presigned media URLs use
    elsewhere. Either way it's the same JWT verification as get_current_user.
    """
    auth_header = request.headers.get("Authorization")
    jwt_token = None
    if auth_header and auth_header.startswith("Bearer "):
        jwt_token = auth_header.split(" ", 1)[1]
    elif token:
        jwt_token = token

    if not jwt_token:
        raise HTTPException(status_code=401, detail="Authentication required")

    email = verify_token(jwt_token)
    if not email:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = UserOperations.get_by_email(email)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def _verify_story_access(story_id: str, user: dict, allow_public: bool = False) -> bool:
    """True if user owns story_id (saved or in-progress) or is an admin.

    `allow_public=True` additionally admits a saved story whose owner ticked
    "make discoverable". Pass it ONLY on read paths - viewing, streaming media,
    polling status. Save, delete, export and regenerate stay owner-only, because
    otherwise sharing a story would also hand over the right to destroy it.
    """
    if user.get("is_admin"):
        return True
    # Saved stories: StoryOperations.get_story already scopes to the owner.
    if StoryOperations.get_story(story_id, user):
        return True
    if allow_public and StoryOperations.is_public_story(story_id):
        return True
    # In-progress/generated stories live in job_state.db, not MySQL. They are
    # never public: consent is given when saving, and these are not saved yet.
    status = job_manager.get_story_status(story_id)
    if status and status.get("user_id") == user.get("id"):
        return True
    return False


def _resolve_visible_duplicate(duplicate_info: dict, viewer: dict) -> Optional[Dict[str, Any]]:
    """Describe a hash match to `viewer`, or return None if they may not see it.

    Ownership is read from MySQL on every call - never from the hash cache and
    never from the on-disk metadata.json, which records whoever generated the
    file rather than who owns the saved row.
    """
    saved_ids = [
        m["story_id"] for m in (duplicate_info.get("saved_stories") or [])
        if m.get("story_id")
    ]
    row = StoryOperations.resolve_visible_duplicate(saved_ids, viewer)
    if row:
        created_at = row.get("created_at")
        return {
            "is_duplicate": True,
            "duplicate_type": "saved",
            "story_id": row["story_id"],
            "story_title": row["name"],
            "created_at": created_at.isoformat() if created_at else None,
            "created_by": row["username"],
            "is_own": bool(row["is_own"]),
            "is_public": bool(row["is_public"]),
            "file_hash": duplicate_info.get("hash"),
        }

    # Generated stories are still being produced and nobody has consented to
    # share them yet, so they surface to their own owner only. Most recent first.
    for match in reversed(duplicate_info.get("generated_stories") or []):
        story_id = match.get("story_id")
        if not story_id:
            continue
        status = job_manager.get_story_status(story_id)
        if not status or status.get("user_id") != viewer.get("id"):
            continue
        return {
            "is_duplicate": True,
            "duplicate_type": "generated",
            "story_id": story_id,
            "story_title": status.get("title") or "Untitled",
            "created_at": status.get("created_at"),
            "created_by": status.get("username") or viewer.get("username"),
            "is_own": True,
            "is_public": False,
            "file_hash": duplicate_info.get("hash"),
        }
    return None



def _audio_media_type(path) -> str:
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
    return ""

@app.get("/api/health")
async def health_check():
    """Liveness + dependency probe for Docker's healthcheck.

    Deliberately checks the things that can fail while the process still looks
    alive - MySQL pool, job-state SQLite, disk. `restart: unless-stopped` only
    catches a dead PID; a uvicorn stuck on an exhausted connection pool stays
    "Up" forever without this.
    """
    import shutil as _shutil
    checks = {}
    healthy = True

    try:
        with get_db_cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["mysql"] = "ok"
    except Exception as e:
        checks["mysql"] = f"error: {type(e).__name__}"
        healthy = False

    try:
        job_manager.get_story_status("healthcheck-probe")
        checks["job_state"] = "ok"
    except Exception as e:
        checks["job_state"] = f"error: {type(e).__name__}"
        healthy = False

    try:
        usage = _shutil.disk_usage("/")
        free_gb = round(usage.free / 1073741824, 1)
        checks["disk_free_gb"] = free_gb
        # Below 2GB a single story (images + audio) can fail mid-write.
        if free_gb < 2:
            checks["disk"] = "critical"
            healthy = False
        else:
            checks["disk"] = "ok"
    except Exception as e:
        checks["disk"] = f"error: {type(e).__name__}"

    body = {
        "status": "healthy" if healthy else "degraded",
        "version": os.getenv("BUILD_TAG", "dev"),
        "checks": checks,
        # Utilisation, not health - a saturated governor is the system working
        # as designed. This is here because a ceiling you cannot observe is a
        # ceiling you cannot tune: peak_wait_s and waiting are what tell you
        # whether raising a limit would change anything.
        "concurrency": governor_snapshot(),
        "vision_budget": vision_budget.snapshot(),
        "queue": generation_queue.stats(),
    }
    if not healthy:
        return JSONResponse(status_code=503, content=body)
    return body


@app.api_route("/api/outputs/{subpath:path}", methods=["GET", "HEAD"])
async def serve_output_file(subpath: str, media_user: dict = Depends(get_media_user)):
    """
    Serve legacy TTS audio cache and job status files from outputs/.
    Kept for backward compatibility with older stories and as the live cache
    used during progressive generation - every file here is named after the
    story_id it belongs to, so ownership is enforced the same way as the
    other media routes.
    """
    if ".." in subpath or subpath.startswith("/") or "\\" in subpath:
        raise HTTPException(status_code=400, detail="Invalid path")

    story_id = None
    m = re.match(r"^audio_cache/audio_([a-f0-9\-]{36})_\d+\.mp3$", subpath)
    if m:
        story_id = m.group(1)
    else:
        m = re.match(r"^status/([a-f0-9\-]{36})\.json$", subpath)
        if m:
            story_id = m.group(1)

    if not story_id:
        raise HTTPException(status_code=404, detail="File not found")

    if not _verify_story_access(story_id, media_user, allow_public=True):
        raise HTTPException(status_code=403, detail="You do not have permission to access this resource.")

    from pathlib import Path
    from fastapi.responses import FileResponse
    file_path = Path("outputs") / subpath
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(file_path, media_type=_audio_media_type(file_path) or None)


@app.api_route("/api/saved-stories/{story_id}/{filename:path}", methods=["GET", "HEAD"])
async def serve_story_file(story_id: str, filename: str, media_user: dict = Depends(get_media_user)):
    """
    Serve story files with smart filename matching and proper CORS headers.
    Handles both GET and HEAD requests.
    Supports both old format (scene_0.png) and new format (uuid_scene_0.png).
    Requires the requester to own the story (or be an admin).
    """
    import glob
    import os
    from pathlib import Path
    from fastapi.responses import FileResponse

    # Validate story_id format (UUID v4)
    if not re.match(r"^[a-f0-9\-]{36}$", story_id):
        raise HTTPException(status_code=400, detail="Invalid story ID format")

    if not _verify_story_access(story_id, media_user, allow_public=True):
        raise HTTPException(status_code=403, detail="You do not have permission to access this story.")

    # Reject path traversal / absolute paths. Legitimate filenames are always
    # flat basenames (e.g. "scene_0.png", "<uuid>_scene_0.png") - never nested.
    if ".." in filename or filename.startswith("/") or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    logger.info(f"📁 File request: story_id={story_id}, filename={filename}")

    # Use storage manager to find the correct directory (handles safe names/UUIDs)
    try:
        story_dir = Path(storage_manager.get_story_path(story_id, in_saved=True))
        logger.info(f"📂 Resolved directory: {story_dir}")
    except Exception as e:
        logger.error(f"❌ Failed to resolve story path: {e}")
        raise HTTPException(status_code=404, detail=f"Story directory not found: {story_id}")
    
    if not story_dir.exists():
        logger.error(f"❌ Story directory does not exist on disk: {story_dir}")
        raise HTTPException(status_code=404, detail=f"Story directory not found: {story_id}")
    
    exact_path = story_dir / filename
    logger.info(f"🔍 Checking exact path: {exact_path}")
    
    # Try exact match first
    if exact_path.exists() and exact_path.is_file():
        logger.info(f"✅ Found exact match: {exact_path}")
        response = FileResponse(exact_path, media_type=_audio_media_type(exact_path) or None)
        # Add CORS headers for media files
        response.headers["Access-Control-Allow-Origin"] = "https://edusmart.ign3el.com"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return response
    
    # If not found, try multiple patterns to support both old and new formats
    
    # Pattern 1: UUID prefix (new format) - {uuid}_scene_0.png
    pattern1 = str(story_dir / f"*_{filename}")
    logger.info(f"🔎 Searching with UUID prefix pattern: {pattern1}")
    matches1 = glob.glob(pattern1)
    
    if matches1:
        logger.info(f"✅ Found UUID-prefixed file: {matches1[0]}")
        response = FileResponse(matches1[0], media_type=_audio_media_type(matches1[0]) or None)
        response.headers["Access-Control-Allow-Origin"] = "https://edusmart.ign3el.com"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return response
    
    # Pattern 2: Old format direct match - scene_0.png
    # If filename is like "abc123_scene_0.png", try "scene_0.png"
    if "_scene_" in filename:
        base_filename = filename.split("_scene_")[-1]
        old_format_path = story_dir / f"scene_{base_filename}"
        logger.info(f"🔎 Trying old format: {old_format_path}")
        
        if old_format_path.exists() and old_format_path.is_file():
            logger.info(f"✅ Found old format file: {old_format_path}")
            response = FileResponse(old_format_path, media_type=_audio_media_type(old_format_path) or None)
            response.headers["Access-Control-Allow-Origin"] = "https://edusmart.ign3el.com"
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            return response
    
    # Pattern 3: Reverse - if requesting old format, try new format
    if filename.startswith("scene_"):
        # Extract scene number and type
        parts = filename.split("_")
        if len(parts) >= 2:
            scene_part = parts[1].split(".")[0]
            ext = filename.split(".")[-1]
            # Try to find any UUID-prefixed file with this scene number
            pattern3 = str(story_dir / f"*_scene_{scene_part}.{ext}")
            logger.info(f"🔎 Trying new format pattern: {pattern3}")
            matches3 = glob.glob(pattern3)
            
            if matches3:
                logger.info(f"✅ Found new format file: {matches3[0]}")
                response = FileResponse(matches3[0], media_type=_audio_media_type(matches3[0]) or None)
                response.headers["Access-Control-Allow-Origin"] = "https://edusmart.ign3el.com"
                response.headers["Access-Control-Allow-Credentials"] = "true"
                response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
                return response
    
    # Pattern 4: Generic wildcard search
    # Try to find any file containing the scene number
    if "scene_" in filename:
        scene_match = filename.split("scene_")[-1].split(".")[0]
        pattern4 = str(story_dir / f"*scene_{scene_match}*")
        logger.info(f"🔎 Trying wildcard pattern: {pattern4}")
        matches4 = glob.glob(pattern4)
        
        if matches4:
            logger.info(f"✅ Found wildcard match: {matches4[0]}")
            response = FileResponse(matches4[0], media_type=_audio_media_type(matches4[0]) or None)
            response.headers["Access-Control-Allow-Origin"] = "https://edusmart.ign3el.com"
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            return response
    
    # List all files in directory for debugging
    all_files = list(story_dir.iterdir())
    logger.error(f"❌ File not found: {filename}")
    logger.error(f"📂 Available files in {story_dir}: {[f.name for f in all_files]}")
    raise HTTPException(status_code=404, detail=f"File not found: {filename}")

@app.api_route("/api/generated-stories/{story_id}/{filename:path}", methods=["GET", "HEAD"])
async def serve_generated_story_file(story_id: str, filename: str, media_user: dict = Depends(get_media_user)):
    """
    Serve files from generated_stories folder with proper CORS headers.
    Similar to saved-stories endpoint but for in-progress/temporary stories.
    Supports both old format (scene_0.png) and new format (uuid_scene_0.png).
    Requires the requester to own the story (or be an admin).
    """
    import glob
    import os
    from pathlib import Path
    from fastapi.responses import FileResponse

    # Validate story_id format (UUID v4)
    if not re.match(r"^[a-f0-9\-]{36}$", story_id):
        raise HTTPException(status_code=400, detail="Invalid story ID format")

    if not _verify_story_access(story_id, media_user):
        raise HTTPException(status_code=403, detail="You do not have permission to access this story.")

    # Reject path traversal / absolute paths. Legitimate filenames are always
    # flat basenames (e.g. "scene_0.png", "<uuid>_scene_0.png") - never nested.
    if ".." in filename or filename.startswith("/") or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    logger.info(f"📁 Generated story file request: story_id={story_id}, filename={filename}")

    # Use storage manager to find the correct directory
    try:
        story_dir = Path(storage_manager.get_story_path(story_id, in_saved=False))
        logger.info(f"📂 Resolved generated directory: {story_dir}")
    except Exception as e:
        logger.error(f"❌ Failed to resolve generated story path: {e}")
        raise HTTPException(status_code=404, detail=f"Generated story directory not found: {story_id}")
    
    if not story_dir.exists():
        logger.error(f"❌ Generated story directory does not exist: {story_dir}")
        raise HTTPException(status_code=404, detail=f"Generated story directory not found: {story_id}")
    
    exact_path = story_dir / filename
    logger.info(f"🔍 Checking exact path: {exact_path}")
    
    # Try exact match first
    if exact_path.exists() and exact_path.is_file():
        logger.info(f"✅ Found exact match: {exact_path}")
        response = FileResponse(exact_path, media_type=_audio_media_type(exact_path) or None)
        # Add CORS headers for media files
        response.headers["Access-Control-Allow-Origin"] = "https://edusmart.ign3el.com"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return response
    
    # If not found, try multiple patterns to support both old and new formats
    
    # Pattern 1: UUID prefix (new format) - {uuid}_scene_0.png
    pattern1 = str(story_dir / f"*_{filename}")
    logger.info(f"🔎 Searching with UUID prefix pattern: {pattern1}")
    matches1 = glob.glob(pattern1)
    
    if matches1:
        logger.info(f"✅ Found UUID-prefixed file: {matches1[0]}")
        response = FileResponse(matches1[0], media_type=_audio_media_type(matches1[0]) or None)
        response.headers["Access-Control-Allow-Origin"] = "https://edusmart.ign3el.com"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return response
    
    # Pattern 2: Old format direct match - scene_0.png
    # If filename is like "abc123_scene_0.png", try "scene_0.png"
    if "_scene_" in filename:
        base_filename = filename.split("_scene_")[-1]
        old_format_path = story_dir / f"scene_{base_filename}"
        logger.info(f"🔎 Trying old format: {old_format_path}")
        
        if old_format_path.exists() and old_format_path.is_file():
            logger.info(f"✅ Found old format file: {old_format_path}")
            response = FileResponse(old_format_path, media_type=_audio_media_type(old_format_path) or None)
            response.headers["Access-Control-Allow-Origin"] = "https://edusmart.ign3el.com"
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            return response
    
    # Pattern 3: Reverse - if requesting old format, try new format
    if filename.startswith("scene_"):
        # Extract scene number and type
        parts = filename.split("_")
        if len(parts) >= 2:
            scene_part = parts[1].split(".")[0]
            ext = filename.split(".")[-1]
            # Try to find any UUID-prefixed file with this scene number
            pattern3 = str(story_dir / f"*_scene_{scene_part}.{ext}")
            logger.info(f"🔎 Trying new format pattern: {pattern3}")
            matches3 = glob.glob(pattern3)
            
            if matches3:
                logger.info(f"✅ Found new format file: {matches3[0]}")
                response = FileResponse(matches3[0], media_type=_audio_media_type(matches3[0]) or None)
                response.headers["Access-Control-Allow-Origin"] = "https://edusmart.ign3el.com"
                response.headers["Access-Control-Allow-Credentials"] = "true"
                response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
                return response
    
    # Pattern 4: Generic wildcard search
    # Try to find any file containing the scene number
    if "scene_" in filename:
        scene_match = filename.split("scene_")[-1].split(".")[0]
        pattern4 = str(story_dir / f"*scene_{scene_match}*")
        logger.info(f"🔎 Trying wildcard pattern: {pattern4}")
        matches4 = glob.glob(pattern4)
        
        if matches4:
            logger.info(f"✅ Found wildcard match: {matches4[0]}")
            response = FileResponse(matches4[0], media_type=_audio_media_type(matches4[0]) or None)
            response.headers["Access-Control-Allow-Origin"] = "https://edusmart.ign3el.com"
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            return response
    
    # List all files in directory for debugging
    all_files = list(story_dir.iterdir())
    logger.error(f"❌ File not found. Available files: {[f.name for f in all_files]}")
    raise HTTPException(status_code=404, detail=f"File not found: {filename}")

# .doc/.ppt (legacy pre-2007 binary Office formats) are deliberately excluded -
# python-docx/python-pptx can only read the modern zip/XML .docx/.pptx formats,
# so accepting the legacy ones used to silently generate a story from empty
# extracted text. Reject them here, at the door, with a clear message, instead
# of failing invisibly deep in the pipeline.
_ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt", ".md", ".csv", ".json", ".xml", ".html"}
_MAX_UPLOAD_SIZE_BYTES = 20 * 1024 * 1024  # 20MB


# Uncompressed-size ceiling for the ZIP-based Office formats (.docx/.pptx are
# ZIP archives). python-docx/python-pptx decompress into memory with no regard
# for the ratio, so a well-formed 20MB upload can expand to gigabytes and OOM
# the process - a classic zip bomb. 300MB is far above any real lesson document
# while staying survivable.
_MAX_UNCOMPRESSED_ARCHIVE_BYTES = 300 * 1024 * 1024

# Magic bytes per extension. Extension alone is a claim by the uploader, not a
# fact about the bytes; the parsers downstream trust the type they are handed.
_UPLOAD_MAGIC = {
    ".pdf": (b"%PDF-",),
    # OOXML formats are ZIP archives - both start with a local file header.
    # PK\x03\x04 is a normal archive; PK\x05\x06 is a valid *empty* archive.
    ".docx": (b"PK\x03\x04", b"PK\x05\x06"),
    ".pptx": (b"PK\x03\x04", b"PK\x05\x06"),
}


def _validate_upload(filename: Optional[str], file_content: bytes):
    """Reject oversized, wrong-type, or archive-bomb uploads before they reach
    the story pipeline. Previously nothing checked either - any size/type was
    read fully into memory and handed to the PDF/text extractor unvalidated."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in _ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext or 'unknown'}'. Allowed: {', '.join(sorted(_ALLOWED_UPLOAD_EXTENSIONS))}",
        )
    if len(file_content) > _MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(file_content) / 1024 / 1024:.1f}MB). Max size is {_MAX_UPLOAD_SIZE_BYTES // 1024 // 1024}MB.",
        )

    # Content must match the claimed extension. Only enforced for the binary
    # formats: the text extensions (.txt/.md/.csv/.json/.xml/.html) have no
    # reliable signature and are decoded as text anyway, so there is nothing
    # to spoof into.
    expected_magic = _UPLOAD_MAGIC.get(ext)
    if expected_magic and not any(file_content.startswith(sig) for sig in expected_magic):
        raise HTTPException(
            status_code=400,
            detail=f"File content does not match its '{ext}' extension. The file may be corrupted or renamed.",
        )

    # Decompression-bomb guard for the ZIP-based formats. Reads only the central
    # directory (no extraction), so this is cheap and never materializes the
    # payload it is protecting against.
    if ext in (".docx", ".pptx"):
        import zipfile
        try:
            with zipfile.ZipFile(io.BytesIO(file_content)) as zf:
                total_uncompressed = sum(info.file_size for info in zf.infolist())
        except zipfile.BadZipFile:
            raise HTTPException(
                status_code=400,
                detail=f"This {ext} file is not a readable archive. It may be corrupted.",
            )
        if total_uncompressed > _MAX_UNCOMPRESSED_ARCHIVE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"This file expands to {total_uncompressed / 1024 / 1024:.0f}MB when opened, "
                    f"which exceeds the {_MAX_UNCOMPRESSED_ARCHIVE_BYTES // 1024 // 1024}MB limit."
                ),
            )


def _safe_story_dirname(story_name: str, story_id: str) -> str:
    """Create a filesystem-friendly folder name; fallback to ID fragment on collision/empty."""
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", story_name.strip().lower()) if story_name else ""
    slug = slug.strip("-") or f"story-{story_id[:8]}"
    if os.path.exists(os.path.join("saved_stories", slug)):
        slug = f"{slug}-{story_id[:8]}"
    return slug

async def generate_remaining_tts(story_id: str, scenes: list, scene_ids: list, voice: str, storage_manager, job_manager, grade_level: Optional[str] = None):
    """Generate TTS for scenes 1-9 in background using progressive batching"""
    try:
        published = set()

        def _publish_scene(scene_num: int) -> bool:
            """Move a finished scene's cached audio into the story folder and mark it
            completed, so /api/status exposes it right away instead of only once the
            whole story is done."""
            if scene_num in published:
                return True
            idx = scene_num - 1  # scene_ids excludes Scene 0
            if idx < 0 or idx >= len(scene_ids):
                return False
            cache_file = f"outputs/audio_cache/audio_{story_id}_{scene_num}.mp3"
            if not os.path.exists(cache_file):
                return False
            try:
                with open(cache_file, 'rb') as f:
                    audio_bytes = f.read()
                aud_url = storage_manager.save_file(story_id, f"scene_{scene_num}.mp3", audio_bytes, in_saved=False)
                job_manager.update_scene_audio(scene_ids[idx], "completed", aud_url)
                published.add(scene_num)
                logger.info(f"✓ Scene {scene_num} audio published ({len(published)}/{len(scene_ids)})")
                return True
            except Exception as e:
                logger.error(f"✗ Failed to publish scene {scene_num} audio: {e}")
                return False

        async def on_scene_ready(scene_num: int):
            await asyncio.to_thread(_publish_scene, scene_num)

        # Use gemini's progressive TTS method
        await gemini.generate_progressive_tts(
            story_id=story_id,
            scenes=scenes,
            voice=voice,
            batch_size=2,
            max_threads_per_tts=1,
            on_scene_ready=on_scene_ready,
            grade_level=grade_level
        )

        # Safety net - anything the callback missed still gets published (or marked
        # failed) here, so a callback hiccup can't leave a scene stuck forever.
        for i, scene_id in enumerate(scene_ids):
            scene_num = i + 1  # +1 because Scene 0 is already done
            if scene_num in published:
                continue
            if not _publish_scene(scene_num):
                job_manager.update_scene_audio(scene_id, "failed")
                logger.error(f"✗ Scene {scene_num} audio not found in cache")

        logger.info(f"✓ All TTS generation complete for story {story_id}")

    except Exception as e:
        logger.error(f"✗ Background TTS generation error: {e}")

async def run_ai_workflow_progressive_mobile(story_id: str, file_path: str, grade_level: str, voice: str, speed: float, is_mobile: bool, user_id: int):
    """
    Progressive story generation workflow:
    1. Create story folder in generated_stories
    2. Generate story structure
    3. Create scene records immediately
    4. Process scenes in parallel (images + audio per scene)
    """
    workflow_start_time = time.time()
    # Background tasks spawned by this workflow. The queue slot has to stay
    # held until they finish: otherwise a worker would report the story done
    # the moment Scene 0 was ready and immediately claim the next job, while
    # this story's remaining images and narration were still running. Ten
    # workers would then have thirty stories genuinely in flight, which is the
    # exact unbounded fan-out the queue exists to prevent.
    pending_tasks: List[asyncio.Task] = []
    try:
        # Create story folder with metadata
        storage_manager.create_story_folder(story_id, {
            "grade_level": grade_level,
            "voice": voice,
            "speed": speed,
            "file_path": file_path
        })
        logger.info(f"📁 Created story folder: generated_stories/{story_id}")
        
        # How many quiz questions the user asked for. Read back from job state
        # rather than threaded through the queue, so the queue's handler
        # signature stays as it is; initialize_story() recorded it at upload.
        _job_row = job_manager.get_story_status(story_id) or {}
        quiz_size = normalize_quiz_size(_job_row.get("quiz_size") or DEFAULT_QUIZ_SIZE)

        # Generate story structure
        # Groq's SDK is synchronous, so this occupies a thread-pool slot for the
        # entire round trip, and Groq itself rate-limits by tokens per minute.
        # The governor keeps a burst of simultaneous uploads from spending the
        # whole TPM budget in one second and failing all of them together.
        from services.concurrency import llm_governor

        # One automatic retry. The model returning a malformed or incomplete
        # story is a dice roll, not a property of the document - failing the
        # user's upload on a single bad roll (and refunding a credit they would
        # rather have spent on a working story) is the wrong trade.
        #
        # Verdict-style failures are excluded via failure_reasons.is_retryable:
        # re-asking whether a bank receipt is educational costs a full
        # generation to arrive at the same answer.
        story_data = None
        last_error: Optional[Exception] = None
        for attempt in (1, 2):
            try:
                async with llm_governor.slot():
                    story_data = await asyncio.to_thread(
                        gemini.process_file_to_story, file_path, grade_level, user_id, quiz_size
                    )
                if story_data:
                    break
                last_error = Exception("AI failed to generate story content.")
            except Exception as gen_error:
                last_error = gen_error
                if not failure_reasons.is_retryable(gen_error):
                    logger.info(
                        f"Story {story_id}: not retrying "
                        f"{failure_reasons.classify(gen_error).code} - a retry cannot change it"
                    )
                    raise

            if attempt == 1:
                logger.warning(f"Story {story_id}: attempt 1 failed ({last_error}); retrying once")
                job_manager.mark_story_retrying(story_id)
                # Groq bills prompt + max_tokens against one per-minute budget and
                # attempt 1 has just spent most of it. Going straight into attempt
                # 2 would 429 on arrival and burn the retry for nothing.
                await asyncio.sleep(RETRY_COOLDOWN_SECONDS)

        if not story_data:
            raise last_error or Exception("AI failed to generate story content.")
        
        scenes = story_data.get("scenes", [])
        title = story_data.get("title", "Untitled Story")
        quiz = story_data.get("quiz", [])

        # Joined once per story, not re-derived per scene: the model defines each
        # recurring character's fixed appearance once (unified_prompt's CHARACTER
        # CONSISTENCY requirement) and repeats it verbatim inside each scene's own
        # image_prompt already, so this column just needs to carry that same
        # description for reference/debugging - it previously duplicated
        # image_prompt here instead of holding anything character-specific.
        characters = story_data.get("characters", [])
        character_descriptions = "; ".join(
            f"{c.get('name', '')}: {c.get('description', '')}"
            for c in characters if isinstance(c, dict) and c.get("description")
        )

        # Initialize job state with quiz data
        job_manager.update_story_metadata(story_id, title, len(scenes), quiz)

        # Create scene records immediately (text is ready)
        scene_ids = []
        for i, scene in enumerate(scenes):
            scene_id = job_manager.create_scene(
                story_id,
                i,
                scene.get("narrative_text", ""),
                character_descriptions
            )
            scene_ids.append(scene_id)
        
        logger.info(f"✓ Story structure ready in {time.time() - workflow_start_time:.2f}s: {len(scenes)} scenes")
        
        # --- Optimized Generation Strategy ---
        story_seed = int(uuid.uuid4().hex[:8], 16)
        force_mobile = True
        
        # Define background task for remaining images (Scenes 1..N)
        async def generate_remaining_images_background(r_scenes, seed, mobile, reference_image=None):
            logger.info(f"🎨 Background generating images for {len(r_scenes)} scenes...")
            try:
                images_map = await gemini.generate_images_parallel(
                    r_scenes,
                    seed,
                    # max_workers now defaults to MAX_IMAGES_PER_STORY so the
                    # per-story cap is an env knob, not a literal buried here.
                    is_mobile=mobile,
                    start_index=1,
                    grade_level=grade_level,
                    reference_image=reference_image
                )
                for idx, img_bytes in images_map.items():
                    if img_bytes:
                        img_name = f"scene_{idx}.png"
                        try:
                            url = storage_manager.save_file(story_id, img_name, img_bytes, in_saved=False)
                            job_manager.update_scene_image(scene_ids[idx], "completed", url)
                            logger.info(f"✓ Scene {idx} image saved (background)")
                        except Exception as e:
                            logger.error(f"Failed save scene {idx}: {e}")
                            job_manager.update_scene_image(scene_ids[idx], "failed")
                    else:
                        job_manager.update_scene_image(scene_ids[idx], "failed")
            except Exception as e:
                logger.error(f"Background image gen error: {e}")

        # Define Scene 0 Image Task with Immediate Chaining
        async def generate_scene_0_image_and_chain():
            logger.info("🎨 Starting Scene 0 image generation...")
            img_0 = await gemini.generate_image(
                scenes[0]['image_prompt'],
                story_seed=story_seed,
                is_mobile=force_mobile,
                scene_num=0,
                grade_level=grade_level
            )
            
            # 🔗 CRITICAL: Trigger background generation IMMEDIATELY after Scene 0 Image finishes
            # This prevents RunPod 10s idle timeout if TTS takes longer than Image gen
            if len(scenes) > 1:
                logger.info("🔗 Chaining background image generation immediately...")
                # img_0 becomes the visual anchor for every later scene: a
                # recurring character is then conditioned on the same actual
                # pixels, not merely the same written description. If scene 0
                # failed, img_0 is None and the batch falls back to plain
                # text-to-image rather than blocking the whole story.
                _bg_img_task = asyncio.create_task(
                    generate_remaining_images_background(
                        scenes[1:], story_seed, force_mobile, reference_image=img_0
                    )
                )
                _bg_img_task.add_done_callback(
                    lambda t: logger.error(f"Background image generation failed: {t.exception()}") if t.exception() else None
                )
                pending_tasks.append(_bg_img_task)
                
            return img_0

        logger.info("🚀 Starting parallel Scene 0 Image + Scene 0 TTS generation...")

        # 1. Generate Scene 0 Image (and chain background)
        scene_0_image_task = generate_scene_0_image_and_chain()
        
        # 2. Generate Scene 0 TTS
        tts_0_start_time = time.time()
        async def generate_scene_0_tts_timed():
            # Scene 0 narration goes through the same process-wide TTS governor
            # as scenes 1..N. It is the request the user is actually waiting on,
            # but it is not exempt from the ceiling - letting first scenes bypass
            # the limit is how twenty simultaneous uploads make all twenty slower
            # than any of them queued.
            #
            # It also goes through the SAME CLIENT as scenes 1..N. This used to
            # call chatterbox.generate_audio(), which posts straight at
            # CHATTERBOX_URL and has never once consulted Config.TTS_BACKEND - so
            # with TTS_BACKEND=runpod every other scene ran on the GPU in 2.9-5.7s
            # while scene 0, the only one blocking the player, sat on the CPU
            # container for 26.5s. Route it through kokoro_client like everything
            # else and the backend switch means what it says.
            scene_0_text = scenes[0]['narrative_text']
            scene_0_speed = resolve_grade_spec(grade_level)["tts_speed"]
            async with tts_governor.slot():
                if voice == "ar_teacher":
                    # Arabic goes to Piper, mirroring story_service's scene 1..N
                    # branch - Kokoro has no Arabic voice.
                    from services.piper_client import piper_tts
                    audio = await piper_tts.generate_audio(scene_0_text, speed=scene_0_speed, silence=0.3)
                else:
                    from services.kokoro_client import generate_tts
                    # generate_tts is a blocking requests.post; called bare from
                    # async it stalls the event loop for the whole process.
                    audio = await asyncio.to_thread(
                        generate_tts,
                        text=scene_0_text,
                        voice=voice,
                        speed=scene_0_speed
                    )
            logger.info(f"✓ Scene 0 TTS generated in {time.time() - tts_0_start_time:.2f}s")
            return audio
        scene_0_tts_task = generate_scene_0_tts_timed()

        # Run Scene 0 tasks concurrently
        # Even if TTS takes longer, background images have already started!
        scene_0_data = await asyncio.gather(scene_0_image_task, scene_0_tts_task)
        img_0_bytes, scene_0_audio = scene_0_data

        logger.info(f"✓ Scene 0 assets generated. Saving...")

        # Save Scene 0 Image
        if img_0_bytes:
            img_0_name = "scene_0.png"
            try:
                img_0_url = storage_manager.save_file(story_id, img_0_name, img_0_bytes, in_saved=False)
                job_manager.update_scene_image(scene_ids[0], "completed", img_0_url)
                logger.info(f"✓ Scene 0 image saved: {img_0_url}")
            except Exception as e:
                logger.error(f"✗ Failed to save scene 0 image: {e}")
                job_manager.update_scene_image(scene_ids[0], "failed")
        else:
            job_manager.update_scene_image(scene_ids[0], "failed")
            
        # Save Scene 0 Audio
        if scene_0_audio:
            aud_name = "scene_0.mp3"
            try:
                aud_url = storage_manager.save_file(story_id, aud_name, scene_0_audio, in_saved=False)
                job_manager.update_scene_audio(scene_ids[0], "completed", aud_url)
                logger.info(f"✓ Scene 0 audio saved: {aud_url}")
            except Exception as e:
                logger.error(f"✗ Failed to save Scene 0 audio: {e}")
                job_manager.update_scene_audio(scene_ids[0], "failed")
        else:
            job_manager.update_scene_audio(scene_ids[0], "failed")

        logger.info(f"✓ Story ready for initial display. Background tasks running...")

        # 4. Background: Generate Remaining TTS (Scenes 1..N)
        if len(scenes) > 1:
            pending_tasks.append(asyncio.create_task(
                generate_remaining_tts(story_id, scenes[1:], scene_ids[1:], voice, storage_manager, job_manager, grade_level=grade_level)
            ))
        
        # ✅ CRITICAL FIX: Mark story as completed so frontend can display Scene 0 immediately
        # Note: JobStateManager tracks completion via scene statuses, not a separate status field
        # The story is considered complete when all scenes have images
        logger.info(f"✅ Story {story_id} ready for display — time to first playable scene: {time.time() - workflow_start_time:.2f}s")

        # Hold the queue slot until the rest of the story really is done.
        # Failures inside these tasks are already logged and recorded per scene,
        # so they must not fail the whole story here; return_exceptions also
        # stops one bad scene from cancelling its siblings mid-flight.
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        logger.info(
            f"✅ Story {story_id} fully generated in "
            f"{time.time() - workflow_start_time:.2f}s"
        )

    except Exception as e:
        # The raw exception stays in the logs, where an operator needs it. What
        # reaches the user is the classified, actionable form - see
        # services/failure_reasons for why the generic banner had to go.
        logger.error(f"AI Workflow Error after {time.time() - workflow_start_time:.2f}s: {e}")

        # Refund BEFORE recording the failure, so the message we store can state
        # truthfully whether the credit actually came back. Double-refunding is
        # prevented by the uq_refund_per_story constraint, not by ordering, so
        # it is safe for the reconciler to retry this if we die in between.
        credit_refunded = False
        try:
            refund_credit(user_id, story_id)
            credit_refunded = True
            logger.info(f"Refunded 1 credit to user {user_id} for failed story {story_id}")
        except Exception as refund_error:
            logger.error(f"Failed to refund credit for story {story_id}: {refund_error}")

        details = failure_reasons.describe(e, credit_refunded=credit_refunded)
        logger.error(f"Story {story_id} failed as {details['error_code']}: {details['error']}")
        job_manager.mark_story_failed(story_id, json.dumps(details))

@app.post("/api/check-duplicate")
async def check_duplicate(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)
):
    """Check if a file has been uploaded across both saved and generated stories."""
    # Read file content
    file_content = await file.read()
    _validate_upload(file.filename, file_content)

    # Use hash service to find duplicates
    duplicate_info = hash_service.find_duplicate(file_content, file.filename)
    
    # Reset file pointer for potential reuse
    await file.seek(0)
    
    if duplicate_info:
        # A match on disk is not automatically a match this caller may hear
        # about: the story may belong to somebody who never agreed to share it.
        visible = _resolve_visible_duplicate(duplicate_info, current_user)
        if visible:
            return visible
        logger.info("Hash matched an existing story, but none visible to this user")

    return {"is_duplicate": False, "file_hash": hash_service.generate_bytes_hash(file_content)}

@app.post("/api/upload")
async def upload_story(
    background_tasks: BackgroundTasksExplicit,  # type: ignore
    file: UploadFile = File(...), 
    grade_level: str = Form("4"),
    # How many quiz questions the user asked for. Coerced via normalize_quiz_size
    # rather than validated strictly - an odd value should snap to the nearest
    # offered size, not fail the upload after the file has already been sent.
    quiz_size: int = Form(DEFAULT_QUIZ_SIZE),
    voice: str = Form("af_sarah"),
    speed: float = Form(1.0),
    file_hash: str = Form(None),
    force_new: bool = Form(False),
    user_agent: str = Form(None),
    current_user: User = Depends(get_current_user)
):
    """
    Upload file and handle duplicate detection.
    If duplicate found and user chooses to view existing, delete temp story and redirect.
    If duplicate found and user chooses to generate new, create new story with copy of file.
    """
    # Read file content
    file_content = await file.read()
    _validate_upload(file.filename, file_content)

    # Calculate hash if not provided
    if not file_hash:
        file_hash = hash_service.generate_bytes_hash(file_content)
    
    # Check for duplicate unless force_new is True
    duplicate_info = None
    if not force_new:
        duplicate_info = hash_service.find_duplicate(file_content, file.filename)
    
    if duplicate_info:
        # Same visibility rule as /api/check-duplicate. This branch used to hand
        # back saved_matches[0] unconditionally - another user's story_id and
        # title, attributed to whoever happened to be uploading.
        visible = _resolve_visible_duplicate(duplicate_info, current_user)
        if visible:
            return {
                **visible,
                "file_hash": file_hash,
                "message": "File already exists. Choose to view existing or generate new.",
            }
        # Nothing this user may see - fall through and generate their own copy.
    
    # No duplicate or force_new=True - create new story.
    # Admission control runs first: it rejects before the credit is reserved
    # and before any folder or job-state row exists, so a refused request
    # leaves nothing behind to clean up. 429 + Retry-After is an honest answer;
    # accepting work the system cannot start for an hour is not.
    admit_generation(current_user['id'])

    # Credit check second, still before any temp folder/job state exists, so a
    # blocked (402) request never leaves orphaned state behind.
    check_and_reserve_credit(current_user['id'])

    story_id = str(uuid.uuid4())
    
    # Create temporary story folder in generated_stories
    temp_dir = storage_manager.create_story_folder(story_id, {
        "grade_level": grade_level,
        "voice": voice,
        "speed": speed,
        "original_filename": file.filename,
        "file_hash": file_hash,
        "user_id": current_user['id'],
        "username": current_user['username'],
        "is_temp": True
    })
    
    # Save original file to temp folder
    filename = file.filename or "uploaded_file"
    safe_filename = re.sub(r'[^\w\-.]', '_', filename).lstrip('.')
    temp_file_path = os.path.join(temp_dir, safe_filename)
    with open(temp_file_path, "wb") as f:
        f.write(file_content)
    
    # Store file hash in metadata
    hash_service.update_story_metadata_hash(story_id, file_hash, in_saved=False)
    
    # Initialize job state
    job_manager.initialize_story(
        story_id,
        grade_level,
        file_hash=file_hash,
        user_id=current_user['id'],
        username=current_user['username'],
        quiz_size=normalize_quiz_size(quiz_size),
    )
    
    # Detect if user is on mobile device
    is_mobile = False
    if user_agent:
        mobile_keywords = ['mobile', 'android', 'iphone', 'ipad', 'windows phone', 'blackberry']
        is_mobile = any(keyword in user_agent.lower() for keyword in mobile_keywords)
    
    # Hand the job to the queue instead of running it inline. submit() returns
    # only after the row is committed, so the response can promise a story that
    # actually survives a restart - which add_task could not.
    position = generation_queue.submit(
        story_id,
        current_user['id'],
        {
            "story_id": story_id,
            "file_path": temp_file_path,
            "grade_level": grade_level,
            "voice": voice,
            "speed": speed,
            "is_mobile": is_mobile,
            "user_id": current_user["id"],
        },
    )

    return {
        "job_id": story_id,
        "is_mobile": is_mobile,
        "queue_position": position,
        "message": (
            "Story generation started" if position <= 1
            else f"Queued - {position - 1} story(s) ahead of you"
        ),
    }

@app.post("/api/handle-duplicate-choice")
async def handle_duplicate_choice(
    background_tasks: BackgroundTasks,
    choice: str = Form(...),  # "view_existing" or "generate_new"
    duplicate_story_id: str = Form(...),
    duplicate_type: str = Form(...),  # "saved" or "generated"
    file: UploadFile = File(...),
    grade_level: str = Form("4"),
    quiz_size: int = Form(DEFAULT_QUIZ_SIZE),
    voice: str = Form("af_sarah"),
    speed: float = Form(1.0),
    user_agent: str = Form(None),
    current_user: User = Depends(get_current_user)
):
    """
    Handle user's choice when duplicate is detected.
    - view_existing: Delete temp story and return existing story_id
    - generate_new: Create new story with file copy
    """
    if choice == "view_existing":
        # Delete any temp story that was created
        if duplicate_type == "generated":
            # This is a temp story, delete it
            storage_manager.delete_story(duplicate_story_id, in_saved=False)
            job_manager.mark_story_failed(duplicate_story_id, "User chose to view existing story")
        
        return {
            "action": "view_existing",
            "story_id": duplicate_story_id,
            "message": "Redirecting to existing story"
        }
    
    elif choice == "generate_new":
        # Admission control first, then the credit check - both before any
        # temp folder or job-state row exists. See /api/upload for why.
        admit_generation(current_user['id'])
        check_and_reserve_credit(current_user['id'])

        # Create new story with fresh UUID
        new_story_id = str(uuid.uuid4())
        
        # Read file content
        file_content = await file.read()
        _validate_upload(file.filename, file_content)

        # Create new temp story folder
        temp_dir = storage_manager.create_story_folder(new_story_id, {
            "grade_level": grade_level,
            "voice": voice,
            "speed": speed,
            "original_filename": file.filename,
            "file_hash": hash_service.generate_bytes_hash(file_content),
            "user_id": current_user['id'],
            "username": current_user['username'],
            "is_temp": True,
            "note": "Generated despite duplicate - user choice"
        })
        
        # Save file to new temp folder
        filename = file.filename or "uploaded_file"
        safe_filename = re.sub(r'[^\w\-.]', '+', filename).lstrip('.')
        temp_file_path = os.path.join(temp_dir, safe_filename)
        with open(temp_file_path, "wb") as f:
            f.write(file_content)
        
        # Initialize job state
        job_manager.initialize_story(
            new_story_id,
            grade_level,
            file_hash=hash_service.generate_bytes_hash(file_content),
            user_id=current_user['id'],
            username=current_user['username'],
            quiz_size=normalize_quiz_size(quiz_size),
        )
        
        # Detect mobile
        is_mobile = False
        if user_agent:
            mobile_keywords = ['mobile', 'android', 'iphone', 'ipad', 'windows phone', 'blackberry']
            is_mobile = any(keyword in user_agent.lower() for keyword in mobile_keywords)
        
        # Queue generation (see /api/upload for why this is not add_task)
        position = generation_queue.submit(
            new_story_id,
            current_user['id'],
            {
                "story_id": new_story_id,
                "file_path": temp_file_path,
                "grade_level": grade_level,
                "voice": voice,
                "speed": speed,
                "is_mobile": is_mobile,
                "user_id": current_user["id"],
            },
        )

        return {
            "action": "generate_new",
            "job_id": new_story_id,
            "queue_position": position,
            "message": (
                "New story generation started" if position <= 1
                else f"Queued - {position - 1} story(s) ahead of you"
            ),
        }
    
    else:
        raise HTTPException(status_code=400, detail="Invalid choice")


# Progressive endpoints for scene-by-scene loading
@app.get("/api/story/{story_id}/status")
async def get_story_status(story_id: str, current_user: dict = Depends(get_current_user)) -> Dict[str, Any]:
    """Get overall story status with scene completion info."""
    logger.info(f"🔍 Getting story status for: {story_id}")

    if not _verify_story_access(story_id, current_user, allow_public=True):
        raise HTTPException(status_code=404, detail="Story not found")

    # First check if story is in the active job system
    status = job_manager.get_story_status(story_id)
    if status:
        logger.info(f"✅ Found story in job manager")
        scenes = job_manager.get_all_scenes(story_id)
        
        # Fallback: Reconstruct URLs for legacy stories with empty audio_url/image_url
        for s in scenes:
            scene_index = s["scene_index"]
            
            # Check if URLs are missing and try to reconstruct them
            if not s.get("audio_url") or not s.get("image_url"):
                logger.info(f"🔧 Scene {scene_index} has missing URLs, attempting reconstruction...")
                
                # Check both generated_stories and saved_stories
                for in_saved in [False, True]:
                    story_exists = storage_manager.story_exists(story_id, in_saved=in_saved)
                    if not story_exists:
                        continue
                    
                    story_dir = storage_manager.get_story_path(story_id, in_saved=in_saved)
                    base_path = "saved-stories" if in_saved else "generated-stories"
                    logger.info(f"📂 Checking {base_path}/{story_id} for scene {scene_index} files...")
                    
                    # Try to find audio file if missing
                    if not s.get("audio_url"):
                        # Try multiple audio file patterns
                        audio_patterns = [
                            f"scene_{scene_index}.wav",
                            f"scene_{scene_index}.mp3",
                            f"*_scene_{scene_index}.wav",
                            f"*_scene_{scene_index}.mp3"
                        ]
                        
                        for pattern in audio_patterns:
                            import glob
                            matches = glob.glob(os.path.join(story_dir, pattern))
                            if matches:
                                audio_filename = os.path.basename(matches[0])
                                s["audio_url"] = f"/api/{base_path}/{story_id}/{audio_filename}"
                                logger.info(f"✅ Reconstructed audio URL: {s['audio_url']}")
                                break
                    
                    # Try to find image file if missing
                    if not s.get("image_url"):
                        # Try multiple image file patterns
                        image_patterns = [
                            f"scene_{scene_index}.png",
                            f"scene_{scene_index}.jpg",
                            f"*_scene_{scene_index}.png",
                            f"*_scene_{scene_index}.jpg"
                        ]
                        
                        for pattern in image_patterns:
                            matches = glob.glob(os.path.join(story_dir, pattern))
                            if matches:
                                image_filename = os.path.basename(matches[0])
                                s["image_url"] = f"/api/{base_path}/{story_id}/{image_filename}"
                                logger.info(f"✅ Reconstructed image URL: {s['image_url']}")
                                break
                    
                    # If we found both URLs, no need to check the other location
                    if s.get("audio_url") and s.get("image_url"):
                        break
        
        return {
            "story_id": story_id,
            "status": status["status"],
            "title": status["title"],
            "total_scenes": status["total_scenes"],
            "completed_scenes": status["completed_scenes"],
            "scenes": [
                {
                    "scene_index": s["scene_index"],
                    "text": s["text"],
                    "image_status": s["image_status"],
                    "audio_status": s["audio_status"],
                    "image_url": s["image_url"],
                    "audio_url": s["audio_url"]
                }
                for s in scenes
            ]
        }
    
    # If not in job system, check if it's a saved story
    logger.info(f"📂 Not in job manager, checking saved stories...")
    try:
        story_exists = storage_manager.story_exists(story_id, in_saved=True)
        logger.info(f"📂 Story exists in saved: {story_exists}")
        
        if story_exists:
            # This is a saved story - reconstruct from directory
            metadata = storage_manager.get_metadata(story_id, in_saved=True)
            logger.info(f"📋 Metadata: {metadata}")
            
            # Check if metadata contains complete story_data (new format)
            if metadata and "story_data" in metadata and "scenes" in metadata["story_data"]:
                story_data = metadata["story_data"]
                logger.info(f"✅ Loading from metadata.story_data with {len(story_data.get('scenes', []))} scenes")
                
                # Parse quiz data if it's stored as JSON string
                quiz_data = story_data.get("quiz", [])
                if isinstance(quiz_data, str):
                    try:
                        quiz_data = json.loads(quiz_data)
                    except json.JSONDecodeError:
                        quiz_data = []
                
                return {
                    "story_id": story_id,
                    "status": "completed",
                    "title": story_data.get("title", metadata.get("name", "Saved Story")),
                    "total_scenes": len(story_data.get("scenes", [])),
                    "completed_scenes": len(story_data.get("scenes", [])),
                    "scenes": [
                        {
                            "scene_index": idx,
                            "text": scene.get("text", ""),
                            "image_status": "completed",
                            "audio_status": "completed",
                            "image_url": scene.get("imageUrl") or scene.get("image_url", ""),
                            "audio_url": scene.get("audioUrl") or scene.get("audio_url", "")
                        }
                        for idx, scene in enumerate(story_data.get("scenes", []))
                    ],
                    "quiz": quiz_data
                }
            
            # Try to load from story.json if it exists (legacy format)
            story_path = storage_manager.get_story_path(story_id, in_saved=True)
            logger.info(f"📁 Story path: {story_path}")
            
            story_json_path = os.path.join(story_path, "story.json")
            logger.info(f"📄 Checking for story.json at: {story_json_path}")
            logger.info(f"📄 story.json exists: {os.path.exists(story_json_path)}")
            
            if os.path.exists(story_json_path):
                logger.info(f"✅ Loading from story.json")
                with open(story_json_path, 'r', encoding='utf-8') as f:
                    story_data = json.load(f)
                    logger.info(f"📊 Loaded story data with {len(story_data.get('scenes', []))} scenes")
                
                # Parse quiz data if it's stored as JSON string
                quiz_data = story_data.get("quiz", [])
                if isinstance(quiz_data, str):
                    try:
                        quiz_data = json.loads(quiz_data)
                    except json.JSONDecodeError:
                        quiz_data = []
                    
                return {
                    "story_id": story_id,
                    "status": "completed",
                    "title": story_data.get("title", metadata.get("title", "Saved Story")),
                    "total_scenes": len(story_data.get("scenes", [])),
                    "completed_scenes": len(story_data.get("scenes", [])),
                    "scenes": [
                        {
                            "scene_index": idx,
                            "text": scene.get("text", ""),
                            "image_status": "completed",
                            "audio_status": "completed",
                            "image_url": scene.get("imageUrl") or scene.get("image_url", ""),
                            "audio_url": scene.get("audioUrl") or scene.get("audio_url", "")
                        }
                        for idx, scene in enumerate(story_data.get("scenes", []))
                    ],
                    "quiz": quiz_data
                }
            else:
                # No story.json, use new version-aware reconstruction
                logger.warning(f"⚠️ No story.json found, using version-aware reconstruction")
                
                # Use the new reconstruction method
                reconstructed = storage_manager.reconstruct_story_from_files(story_id, in_saved=True)
                
                if reconstructed and reconstructed.get("scenes"):
                    scenes = reconstructed["scenes"]
                    logger.info(f"✅ Reconstructed {len(scenes)} scenes using version-aware method")
                    
                    return {
                        "story_id": story_id,
                        "status": "completed",
                        "title": metadata.get("name", metadata.get("title", "Saved Story")),
                        "total_scenes": len(scenes),
                        "completed_scenes": len(scenes),
                        "scenes": [
                            {
                                "scene_index": scene["scene_number"],
                                "text": "",  # No text available without story.json
                                "image_status": "completed",
                                "audio_status": "completed",
                                "image_url": f"/api/saved-stories/{story_id}/{scene['image_path']}",
                                "audio_url": f"/api/saved-stories/{story_id}/{scene['audio_path']}"
                            }
                            for scene in scenes
                        ]
                    }
                
                # Fallback to old method if reconstruction fails
                logger.warning(f"⚠️ Version-aware reconstruction failed, falling back to legacy method")
                story_dir = storage_manager.get_story_path(story_id, in_saved=True)
                scenes = []
                scene_index = 0
                job_id = metadata.get("job_id", "")
                
                while True:
                    scene_found = False
                    
                    if job_id:
                        image_file = f"{job_id}_scene_{scene_index}.png"
                        audio_file = f"{job_id}_scene_{scene_index}.mp3"
                        image_path = os.path.join(story_dir, image_file)
                        audio_path = os.path.join(story_dir, audio_file)
                        
                        if not os.path.exists(audio_path):
                            audio_file = f"{job_id}_scene_{scene_index}.wav"
                            audio_path = os.path.join(story_dir, audio_file)
                        
                        if os.path.exists(image_path) or os.path.exists(audio_path):
                            scene_found = True
                    else:
                        image_file = f"scene_{scene_index}.png"
                        audio_file = f"scene_{scene_index}.wav"
                        image_path = os.path.join(story_dir, image_file)
                        audio_path = os.path.join(story_dir, audio_file)
                        
                        if os.path.exists(image_path) or os.path.exists(audio_path):
                            scene_found = True
                    
                    if not scene_found:
                        break
                    
                    scenes.append({
                        "scene_index": scene_index,
                        "text": "",
                        "image_status": "completed" if os.path.exists(image_path) else "missing",
                        "audio_status": "completed" if os.path.exists(audio_path) else "missing",
                        "image_url": f"/api/saved-stories/{story_id}/{image_file}" if os.path.exists(image_path) else "",
                        "audio_url": f"/api/saved-stories/{story_id}/{audio_file}" if os.path.exists(audio_path) else ""
                    })
                    scene_index += 1
                
                return {
                    "story_id": story_id,
                    "status": "completed",
                    "title": metadata.get("name", metadata.get("title", "Saved Story")),
                    "total_scenes": len(scenes),
                    "completed_scenes": len(scenes),
                    "scenes": scenes
                }
    except Exception as e:
        logger.error(f"❌ Error loading saved story {story_id}: {e}")
        logger.exception(e)
    
    # Story not found in either system
    raise HTTPException(status_code=404, detail="Story not found")


@app.get("/api/story/{story_id}/scene/{scene_index}")
async def get_scene_status(story_id: str, scene_index: int, current_user: dict = Depends(get_current_user)) -> Dict[str, Any]:
    """Get specific scene data."""
    if not _verify_story_access(story_id, current_user, allow_public=True):
        raise HTTPException(status_code=404, detail="Story not found")

    scene_id = f"{story_id}_scene_{scene_index}"
    scene = job_manager.get_scene(scene_id)
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found")
    
    return {
        "scene_index": scene["scene_index"],
        "text": scene["text"],
        "image_status": scene["image_status"],
        "audio_status": scene["audio_status"],
        "image_url": scene["image_url"],
        "audio_url": scene["audio_url"]
    }


def _failure_payload(stored_error) -> Dict[str, Any]:
    """Read back what mark_story_failed wrote, in either format.

    Current failures store the classified dict as JSON. Rows written before that
    change - and any row written by a path that still passes a bare string - hold
    raw internal text, which must never be shown to a user verbatim (it leaks
    validator internals and provider names). Those are re-classified on read, so
    an old story in the DB still produces a decent message rather than a
    stack-trace fragment in the UI.
    """
    if not stored_error:
        return failure_reasons.describe("", credit_refunded=True)
    try:
        parsed = json.loads(stored_error)
        if isinstance(parsed, dict) and "error_code" in parsed:
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return failure_reasons.describe(stored_error, credit_refunded=True)


@app.get("/api/status/{job_id}")
async def get_status(job_id: str, current_user: dict = Depends(get_current_user)) -> Dict[str, Any]:
    if not _verify_story_access(job_id, current_user, allow_public=True):
        raise HTTPException(status_code=404, detail="Story not found")

    # Check if it's a progressive story
    status = job_manager.get_story_status(job_id)
    if status:
        scenes = job_manager.get_all_scenes(job_id)

        # Publish every scene that has TEXT, with image_url/audio_url left null
        # until each asset actually lands. This used to require image AND audio
        # both "completed" before a scene appeared at all, and the client only
        # opens the player once it sees one scene - so the user watched a spinner
        # for 34s while the story text had been sitting in SQLite since 7.6s.
        # The picture and the narration now fill in underneath a story the child
        # can already read. StoryPlayer must therefore treat a null image_url or
        # audio_url as "not ready yet", never as an error.
        published_scenes = [
            {
                "text": s["text"],
                "image_url": s["image_url"] if s["image_status"] == "completed" else None,
                "audio_url": s["audio_url"] if s["audio_status"] == "completed" else None
            }
            for s in scenes
            if s["text"]
        ]

        # Progress and completed_scene_count keep their original meaning: a scene
        # counts only once BOTH its assets exist. A text-only scene must not count
        # as progress or the client's stall detector (which watches this number
        # for forward motion) would see a full story the instant the LLM returns
        # and then nothing for another minute.
        fully_ready = sum(
            1 for s in scenes
            if s["image_status"] == "completed" and s["audio_status"] == "completed"
        )
        completed_scenes = published_scenes

        # Progress counts each ASSET, not each finished scene.
        #
        # This used to be `fully_ready / total_scenes`, which meant the number sat
        # at exactly 0% for the whole LLM phase AND the whole first image render -
        # the longest stretch of a generation - then jumped in coarse steps.
        # Confirmed on a real run 2026-08-03: a full minute of "0% BUILDING" while
        # the captions cycled through "writing your story" and "painting the
        # pictures", because not one scene had both assets yet.
        #
        # Each scene contributes two half-credits, so an image landing moves the
        # bar even while its narration is still rendering. STORY_TEXT_WEIGHT is
        # awarded once the LLM returns, which is a real milestone the user just
        # waited through and the single biggest chunk of wall-clock time.
        STORY_TEXT_WEIGHT = 15
        total_scenes = status["total_scenes"] or 0
        if total_scenes > 0:
            assets_done = sum(
                (1 if s["image_status"] == "completed" else 0)
                + (1 if s["audio_status"] == "completed" else 0)
                for s in scenes
            )
            asset_fraction = assets_done / (total_scenes * 2)
            actual_progress = int(STORY_TEXT_WEIGHT + asset_fraction * (100 - STORY_TEXT_WEIGHT))
        else:
            # Scene count is unknown until the LLM returns, so there is genuinely
            # nothing to measure yet. Report 0 and let the client show an
            # indeterminate state rather than inventing a fake ramp.
            actual_progress = 0
        # Never report done-but-not-done: the client treats 100% as terminal.
        if status["status"] != "completed":
            actual_progress = min(actual_progress, 99)
        
        # Parse quiz data if it's stored as JSON string
        quiz_data = status.get("quiz", [])
        if isinstance(quiz_data, str):
            try:
                quiz_data = json.loads(quiz_data)
            except json.JSONDecodeError:
                quiz_data = []
        
        payload = {
            "status": status["status"],
            "progress": actual_progress,
            "total_scenes": status["total_scenes"],  # Always include total count
            "completed_scene_count": fully_ready,  # Scenes with BOTH image and audio
            # Queried only while the job could still be waiting. Every client
            # polls this endpoint every 2 seconds, and there is nothing to look
            # up once a worker has actually picked the story up.
            "queue_position": (
                job_manager.queue_position(job_id)
                if status["status"] == "initializing" else 0
            ),
            # >1 means the first generation attempt failed and a second is running.
            # The client uses this to explain the extra ~60s instead of showing
            # unexplained dead air.
            "attempt": status.get("attempt") or 1,
            "quiz_size": status.get("quiz_size") or DEFAULT_QUIZ_SIZE,
            "result": {
                "title": status["title"],
                "scenes": completed_scenes,
                "quiz": quiz_data
            }
        }

        # The failure detail. This endpoint used to omit it entirely, so the
        # client's `job.error || 'AI Generation failed.'` fallback was the ONLY
        # message any user ever saw, for every cause. The column was being
        # written the whole time; nothing read it back out.
        if status["status"] == "failed":
            payload.update(_failure_payload(status.get("error")))

        # A quiz that came up short of what the user asked for is a note on a
        # delivered story, not an error - the story is complete and playable.
        requested = status.get("quiz_size")
        if status["status"] == "completed" and requested and len(quiz_data) < int(requested):
            payload["quiz_notice"] = (
                f"This quiz has {len(quiz_data)} questions instead of the "
                f"{requested} you asked for - the document didn't have enough "
                f"distinct material for more."
            )

        return payload
    
    # Fall back to old job system
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return jobs[job_id]

@app.patch("/api/stories/{story_id}/visibility")
async def set_story_visibility(
    story_id: str,
    is_public: bool = Body(..., embed=True),
    user: User = Depends(get_current_user),
):
    """Turn discoverability on or off for a saved story.

    Turning it off is not retroactive - it stops the story appearing in future
    duplicate checks, but anyone who already loaded it keeps what they have.
    That is stated plainly in the UI, because a checkbox that implies recall it
    cannot deliver is worse than no checkbox.
    """
    if not StoryOperations.set_visibility(story_id, user, is_public):
        raise HTTPException(status_code=404, detail="Story not found or you do not have permission to change it.")
    logger.info(f"Story {story_id} visibility set to is_public={is_public} by user {user.get('id')}")
    return {"story_id": story_id, "is_public": is_public}


@app.post("/api/save-story/{job_id}")
async def save_story(
    job_id: str,
    story_name: str = Form(...),
    make_public: bool = Form(False),
    user: User = Depends(get_current_user),
):
    """
    Saves a generated story to the database, associating it with the current user.
    This also moves the story's assets from temporary storage to permanent storage.
    """
    if not _verify_story_access(job_id, user):
        raise HTTPException(status_code=404, detail="Story not found")

    # Check if it's a progressive story
    status = job_manager.get_story_status(job_id)
    if status and status["status"] in ("completed", "processing"):
        # Saving MOVES the story folder (shutil.move in move_to_saved). While
        # generation is still running the TTS worker holds the old path, so any
        # scene published after the move lands nowhere and its narration is lost
        # with no error surfaced to the user. Refuse until the story is finished.
        # job_state.py:192 sets "completed" only when every scene has both its
        # image and its audio, and /api/export-job already gates on exactly this.
        if status["status"] != "completed":
            all_scenes = job_manager.get_all_scenes(job_id)
            ready = sum(
                1 for s in all_scenes
                if s["image_status"] == "completed" and s["audio_status"] == "completed"
            )
            total = status.get("total_scenes") or len(all_scenes)
            logger.warning(
                f"Rejected save of in-progress story {job_id} ({ready}/{total} scenes ready)"
            )
            raise HTTPException(
                status_code=409,
                detail=f"Story is still generating ({ready} of {total} scenes ready). "
                       "Please wait until it finishes, then save.",
            )

        # Progressive story system - move folder from generated_stories to saved_stories
        saved_story_id = str(uuid.uuid4())
        scenes = job_manager.get_all_scenes(job_id)
        
        if not scenes:
            raise HTTPException(status_code=404, detail="No scenes available to save.")
        
        # Move the entire folder from generated_stories to saved_stories
        try:
            storage_manager.move_to_saved(job_id, saved_story_id)
            logger.info(f"✅ Moved story folder: {job_id} -> saved_stories/{saved_story_id}")
        except Exception as move_error:
            logger.error(f"Failed to move story folder: {move_error}")
            raise HTTPException(status_code=500, detail=f"Failed to move story files: {move_error}")
        
        # Update URLs from generated-stories to saved-stories
        updated_scenes = []
        for idx, s in enumerate(scenes):
            scene_data = {
                "text": s.get("text", ""),
                "image_url": s.get("image_url") or "",
                "audio_url": s.get("audio_url") or ""
            }
            
            # Update image URL
            if scene_data["image_url"]:
                scene_data["image_url"] = scene_data["image_url"].replace(
                    f"/api/generated-stories/{job_id}/",
                    f"/api/saved-stories/{saved_story_id}/"
                )
            
            # Update audio URL
            if scene_data["audio_url"]:
                scene_data["audio_url"] = scene_data["audio_url"].replace(
                    f"/api/generated-stories/{job_id}/",
                    f"/api/saved-stories/{saved_story_id}/"
                )
            
            updated_scenes.append(scene_data)
        
        # Ensure quiz is a proper list, not a JSON string
        quiz_data = status.get("quiz", [])
        if isinstance(quiz_data, str):
            try:
                quiz_data = json.loads(quiz_data)
            except:
                quiz_data = []
        
        story_data = {
            "title": status["title"],
            "scenes": updated_scenes,
            "quiz": quiz_data
        }
        
        success = StoryOperations.save_story(
            user_id=user['id'],
            story_id=saved_story_id,
            name=story_name,
            story_data=story_data,
            is_public=make_public
        )
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to save story to the database.")
        
        logger.info(f"Story {saved_story_id} saved successfully with {len(updated_scenes)} scenes")
        return {"story_id": saved_story_id, "message": "Story saved successfully"}
    
    # Fall back to old system
    if job_id not in jobs or jobs[job_id].get("status") != "completed":
        raise HTTPException(status_code=404, detail="Story generation job not found or not completed.")
    
    try:
        story_data = jobs[job_id]["result"]
        # Generate a new, permanent ID for the story.
        story_id = str(uuid.uuid4())
        
        # Save story to the database, associated with the user
        success = StoryOperations.save_story(
            user_id=user['id'],
            story_id=story_id,
            name=story_name,
            story_data=story_data,
            is_public=make_public
        )

        if not success:
            raise HTTPException(status_code=500, detail="Failed to save story to the database.")

        # --- File System Operations ---
        # Create a safe directory name for the story assets
        folder_name = _safe_story_dirname(story_name, story_id)
        story_dir = os.path.join("saved_stories", folder_name)
        os.makedirs(story_dir, exist_ok=True)

        # Move the original uploaded document, if it exists
        upload_path = jobs[job_id].get("upload_path")
        if upload_path and os.path.exists(upload_path):
            upload_filename = os.path.basename(upload_path).replace(f"{job_id}_", "", 1)
            import shutil
            shutil.move(upload_path, os.path.join(story_dir, upload_filename))
        
        # Move all generated media (images, audio) for this job
        for filename in os.listdir("outputs"):
            if filename.startswith(job_id):
                import shutil
                shutil.move(os.path.join("outputs", filename), os.path.join(story_dir, filename.replace(f"{job_id}_", "", 1)))
        
        # We don't save metadata to a JSON file anymore since it's in the DB.
        
        # Clean up the in-memory job
        del jobs[job_id]

        return {"story_id": story_id, "message": "Story saved successfully"}
    except Exception as e:
        logger.error(f"Failed to save story for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while saving the story.")

@app.get("/api/list-stories")
async def list_stories(user: User = Depends(get_current_user)):
    """
    List stories. Admins see all stories, regular users see only their own.
    """
    try:
        logger.info(f"User requesting stories - ID: {user.get('id')}, Email: {user.get('email')}, Is Admin: {user.get('is_admin')}")
        if user.get('is_admin'):
            logger.info(f"Admin user - fetching all stories")
            stories = StoryOperations.get_all_stories()
            logger.info(f"Admin retrieved {len(stories)} stories")
        else:
            logger.info(f"Regular user - fetching only their stories")
            stories = StoryOperations.get_user_stories(user['id'])
            logger.info(f"User retrieved {len(stories)} stories")
        return stories
    except Exception as e:
        logger.error(f"Failed to list stories: {e}")
        raise HTTPException(status_code=500, detail="Failed to list stories.")

@app.get("/api/load-story/{story_id}")
async def load_story(story_id: str, user: User = Depends(get_current_user)):
    """
    Load a specific story. Enforces ownership rules (users can only load their own
    stories, unless they are an admin).
    """
    logger.info(f"Loading story {story_id} for user {user.get('email')} (admin: {user.get('is_admin')})")
    # Read-only: a story its owner made discoverable can be opened by anyone who
    # has its id. Saving, renaming and deleting still go through the owner-scoped
    # StoryOperations calls, so this does not hand over control of the story.
    story = StoryOperations.get_story(story_id, user, allow_public=True)
    if not story:
        logger.warning(f"Story {story_id} not found or user {user.get('email')} lacks permission")
        raise HTTPException(status_code=404, detail="Story not found or you do not have permission to view it.")
    
    logger.info(f"Successfully loaded story {story_id}: {story.get('name')}")
    # The 'story_data' from the DB needs to have its URLs updated to point to the correct static path
    story_data = story.get("story_data", {})
    
    # Log scenes info for debugging
    scenes = story_data.get("scenes", [])
    logger.info(f"Story has {len(scenes)} scenes")
    
    for idx, scene in enumerate(scenes):
        # Log original URLs
        orig_image = scene.get("image_url", "")
        orig_audio = scene.get("audio_url", "")
        logger.debug(f"Scene {idx} original - image: {orig_image[:50] if orig_image else 'EMPTY'}, audio: {orig_audio[:50] if orig_audio else 'EMPTY'}")
        
        # --- Image URL Fix ---
        if scene.get("image_url"):
            img_url = scene["image_url"]
            # Fix 1: Legacy outputs path
            if "/api/outputs/" in img_url:
                scene["image_url"] = img_url.replace("/api/outputs/", f"/api/saved-stories/{story_id}/")
            # Fix 2: Relative path (e.g. "scene_0.png") -> prepend full API path
            elif not img_url.startswith("http") and not img_url.startswith("/api/") and not img_url.startswith("data:"):
                scene["image_url"] = f"/api/saved-stories/{story_id}/{img_url}"
        else:
            logger.warning(f"Scene {idx} has no image_url!")
            
        # --- Audio URL Fix ---
        if scene.get("audio_url"):
            aud_url = scene["audio_url"]
            # Fix 1: Legacy outputs path
            if "/api/outputs/" in aud_url:
                scene["audio_url"] = aud_url.replace("/api/outputs/", f"/api/saved-stories/{story_id}/")
            # Fix 2: Generated stories path -> saved stories path
            elif "/api/generated-stories/" in aud_url:
                scene["audio_url"] = aud_url.replace("/api/generated-stories/", f"/api/saved-stories/")
            # Fix 3: Relative path (e.g. "scene_0.wav") -> prepend full API path
            elif not aud_url.startswith("http") and not aud_url.startswith("/api/") and not aud_url.startswith("data:"):
                scene["audio_url"] = f"/api/saved-stories/{story_id}/{aud_url}"
        else:
            logger.warning(f"Scene {idx} has no audio_url!")
            
    return {"name": story["name"], "story_data": story_data}

@app.delete("/api/delete-story/{story_id}")
async def delete_story(story_id: str, user: User = Depends(get_current_user)):
    """
    Deletes a specific story. Enforces ownership (users can only delete their own
    stories, unless they are an admin).
    """
    # The StoryOperations.delete_story now handles the permission logic.
    was_deleted = StoryOperations.delete_story(story_id, user)
    
    if not was_deleted:
        raise HTTPException(status_code=404, detail="Story not found or you do not have permission to delete it.")
        
    # Also delete the story from the file system
    story_dir = os.path.join("saved_stories", story_id)
    if os.path.exists(story_dir):
        import shutil
        shutil.rmtree(story_dir)

    return {"message": "Story deleted successfully"}

@app.get("/api/export-job/{job_id}")
async def export_job(job_id: str, current_user: dict = Depends(get_current_user)):
    """
    Export a job (story generation) as a ZIP file for offline use.
    Includes all scenes with images and audio files.
    """
    if not _verify_story_access(job_id, current_user):
        raise HTTPException(status_code=404, detail="Job not found")

    # Check if it's a progressive story
    status = job_manager.get_story_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if status["status"] != "completed":
        raise HTTPException(status_code=400, detail="Job not completed yet")
    
    scenes = job_manager.get_all_scenes(job_id)
    
    # Create ZIP file in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        # Add story metadata
        story_data = {
            "title": status["title"],
            "scenes": []
        }
        
        # Add each scene's assets
        for idx, scene in enumerate(scenes):
            # The bundled name must follow the real file. Hardcoding .wav here
            # shipped mp3 bytes under a .wav name, so the offline player - which
            # picks its decoder from the name - could refuse its own bundle.
            audio_path = ""
            if scene["audio_url"]:
                audio_path = scene["audio_url"].replace("/api/outputs/", "outputs/")
            audio_ext = os.path.splitext(audio_path)[1].lower() or ".mp3"

            scene_data = {
                "text": scene["text"],
                "image_url": f"scene_{idx}.png",
                "audio_url": f"scene_{idx}{audio_ext}"
            }
            story_data["scenes"].append(scene_data)

            # Add image file
            if scene["image_url"]:
                img_path = scene["image_url"].replace("/api/outputs/", "outputs/")
                if os.path.exists(img_path):
                    zip_file.write(img_path, f"scene_{idx}.png")

            # Add audio file
            if audio_path and os.path.exists(audio_path):
                zip_file.write(audio_path, f"scene_{idx}{audio_ext}")
        
        # Add story.json
        zip_file.writestr("story.json", json.dumps(story_data, indent=2))
    
    zip_buffer.seek(0)
    
    safe_title = re.sub(r'[^\w\-]', '_', status.get('title', 'story')).strip('_')[:100]
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={safe_title}.zip"
        }
    )

@app.get("/api/export-story/{story_id}")
async def export_story(story_id: str, user: User = Depends(get_current_user)):
    """
    Export a saved story as a ZIP file for offline use.
    Includes all scenes with images and audio files.
    """
    story = StoryOperations.get_story(story_id, user)
    if not story:
        raise HTTPException(status_code=404, detail="Story not found or you do not have permission to access it.")
    
    story_data = story.get("story_data", {})
    
    # Create ZIP file in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        # Update story data with local file references
        export_data = {
            "title": story_data.get("title", story["name"]),
            "scenes": []
        }
        
        # Add each scene's assets
        for idx, scene in enumerate(story_data.get("scenes", [])):
            scene_data = {
                "text": scene.get("text", ""),
                "image_url": f"scene_{idx}.png",
                "audio_url": f"scene_{idx}.mp3"
            }
            export_data["scenes"].append(scene_data)
            
            # Add image file from saved_stories directory
            if scene.get("image_url"):
                img_path = os.path.join("saved_stories", story_id, f"scene_{idx}.png")
                if os.path.exists(img_path):
                    zip_file.write(img_path, f"scene_{idx}.png")
            
            # Add audio file from saved_stories directory
            if scene.get("audio_url"):
                audio_path = os.path.join("saved_stories", story_id, f"scene_{idx}.mp3")
                if os.path.exists(audio_path):
                    zip_file.write(audio_path, f"scene_{idx}.mp3")
        
        # Add story.json
        zip_file.writestr("story.json", json.dumps(export_data, indent=2))
    
    zip_buffer.seek(0)
    
    safe_title = re.sub(r'[^\w\-]', '_', story.get('name', 'story')).strip('_')[:100]
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={safe_title}.zip"
        }
    )

# --- PROGRESSIVE TTS ENDPOINTS ---

@app.get("/api/story/{story_id}/tts-status")
async def get_tts_status(story_id: str, current_user: dict = Depends(get_current_user)):
    """Get specialized progressive TTS generation status"""
    if not _verify_story_access(story_id, current_user, allow_public=True):
        raise HTTPException(status_code=404, detail="Story not found")
    # Use gemini service's status tracking
    return await gemini.get_tts_status(story_id)

@app.get("/api/story/{story_id}/scene/{scene_num}/audio")
async def get_scene_audio(story_id: str, scene_num: int, current_user: dict = Depends(get_current_user)):
    """Get scene audio (from cache or generated/saved folder) with waterfall fallbacks"""
    if not _verify_story_access(story_id, current_user, allow_public=True):
        raise HTTPException(status_code=404, detail="Story not found")
    import os
    import aiofiles
    from fastapi.responses import FileResponse, Response
    
    # 1. Check progressive TTS cache (fastest)
    # Note: scene_num matches the 1-based index used in file names
    cache_file = f"outputs/audio_cache/audio_{story_id}_{scene_num}.mp3"
    
    if os.path.exists(cache_file):
        return FileResponse(cache_file, media_type=_audio_media_type(cache_file) or "audio/mpeg")
    
    # 2. Check active job (in-memory/generated_stories)
    try:
        story_dir = storage_manager.get_story_path(story_id, in_saved=False)
        
        # Extension order is now mp3-first: that is what we write. .wav stays
        # in the list for stories generated before 2026-07-26.
        for ext in [".mp3", ".wav"]:
            # Try simple name
            simple_path = os.path.join(story_dir, f"scene_{scene_num}{ext}")
            if os.path.exists(simple_path):
                return FileResponse(simple_path, media_type=_audio_media_type(simple_path))
            
            # Try UUID prefixed
            import glob
            matches = glob.glob(os.path.join(story_dir, f"*_scene_{scene_num}{ext}"))
            if matches:
                 return FileResponse(matches[0], media_type=_audio_media_type(matches[0]))
    except Exception:
        pass
        
    # 3. Check saved stories (persistent storage)
    try:
        story_dir = storage_manager.get_story_path(story_id, in_saved=True)
        # mp3-first, .wav retained for pre-2026-07-26 stories.
        for ext in [".mp3", ".wav"]:
            # Try simple name
            simple_path = os.path.join(story_dir, f"scene_{scene_num}{ext}")
            if os.path.exists(simple_path):
                return FileResponse(simple_path, media_type=_audio_media_type(simple_path))
            
            # Try UUID prefixed
            import glob
            matches = glob.glob(os.path.join(story_dir, f"*_scene_{scene_num}{ext}"))
            if matches:
                 return FileResponse(matches[0], media_type=_audio_media_type(matches[0]))
    except Exception:
        pass
    
    # Not found in any location
    raise HTTPException(status_code=404, detail=f"Audio not found for scene {scene_num}")

# --- QUIZ COMPLETION ENDPOINT ---

@app.post("/api/story/{story_id}/complete-quiz")
async def mark_quiz_complete(
    story_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Mark quiz as completed for the current user's story"""
    try:
        with get_db_cursor(commit=True) as cursor:
            # Ownership is established by an explicit SELECT, never by the
            # UPDATE's rowcount. MySQL reports CHANGED rows, not matched rows
            # (the pool does not set CLIENT_FOUND_ROWS), so an idempotent write
            # returns 0 whenever the value is already correct - marking a quiz
            # complete a second time (a retake) looked identical to "you do not
            # own this story" and returned a false 404 to the legitimate owner.
            cursor.execute("""
                SELECT 1 AS found
                FROM user_stories
                WHERE user_id = %s AND story_id = %s
            """, (current_user["id"], story_id))
            if cursor.fetchone() is None:
                raise HTTPException(
                    status_code=404,
                    detail="Story not found or not owned by user"
                )

            cursor.execute("""
                UPDATE user_stories
                SET quiz_completed = TRUE
                WHERE user_id = %s AND story_id = %s
            """, (current_user["id"], story_id))

            # Fetch updated story data
            cursor.execute("""
                SELECT story_id, name, story_data, created_at, quiz_completed
                FROM user_stories
                WHERE user_id = %s AND story_id = %s
            """, (current_user["id"], story_id))

            story = cursor.fetchone()
            
            return {
                "success": True,
                "message": "Quiz marked as completed",
                "story": {
                    "story_id": story["story_id"],
                    "name": story["name"],
                    "quiz_completed": story["quiz_completed"],
                    "created_at": story["created_at"].isoformat() if story["created_at"] else None
                }
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error marking quiz complete: {e}")
        raise HTTPException(status_code=500, detail=str(e))


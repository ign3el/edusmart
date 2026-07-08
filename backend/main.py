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
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException, Depends
from fastapi import BackgroundTasks as BackgroundTasksExplicit
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

# Import the new, clean refactored modules
from database import initialize_database, get_db_cursor
from routers.auth import router as auth_router, get_current_user
from routers.admin import router as admin_router
from routers.upload import router as upload_router
from core.setup import create_admin_user
from database_models import User, StoryOperations
from services.story_service import StoryService
from services.tts_service import kokoro_tts
from services.hash_service import hash_service
from job_state import job_manager
from story_storage import storage_manager, cleanup_scheduler_task, database_cleanup_scheduler_task
from typing import Optional, TYPE_CHECKING, Dict, Any

# Type checking imports for Pylance
if TYPE_CHECKING:
    from services.story_service import StoryService

# Load environment variables from .env file
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- App Initialization ---
app = FastAPI(title="EduStory API")

# Add CORS middleware to allow requests from frontend domain.
# Configured for production with specific origins for better security.
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


# --- Non-Auth related application logic ---

# Type annotation for gemini service to help Pylance
from typing import Dict, Any, List, TYPE_CHECKING

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
# Old outputs directory kept temporarily for backward compatibility
if os.path.exists("outputs"):
    app.mount("/api/outputs", StaticFiles(directory="outputs"), name="outputs")

# Mount generated_stories for temporary/in-progress stories
app.mount("/api/generated-stories", StaticFiles(directory="generated_stories"), name="generated_stories")

# Custom endpoint handles saved-stories with UUID prefix matching (see below)
# app.mount("/api/saved-stories", StaticFiles(directory="saved_stories"), name="saved_stories")

@app.api_route("/api/saved-stories/{story_id}/{filename:path}", methods=["GET", "HEAD"])
async def serve_story_file(story_id: str, filename: str):
    """
    Serve story files with smart filename matching and proper CORS headers.
    Handles both GET and HEAD requests.
    Supports both old format (scene_0.png) and new format (uuid_scene_0.png).
    """
    import glob
    import os
    from pathlib import Path
    from fastapi.responses import FileResponse
    
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
        response = FileResponse(exact_path)
        # Add CORS headers for media files
        response.headers["Access-Control-Allow-Origin"] = "https://edusmart.ign3el.com"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        # Force correct content type for Kokoro wav files if needed
        if filename.endswith(".wav"):
             response.headers["Content-Type"] = "audio/wav"
        return response
    
    # If not found, try multiple patterns to support both old and new formats
    
    # Pattern 1: UUID prefix (new format) - {uuid}_scene_0.png
    pattern1 = str(story_dir / f"*_{filename}")
    logger.info(f"🔎 Searching with UUID prefix pattern: {pattern1}")
    matches1 = glob.glob(pattern1)
    
    if matches1:
        logger.info(f"✅ Found UUID-prefixed file: {matches1[0]}")
        response = FileResponse(matches1[0])
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
            response = FileResponse(old_format_path)
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
                response = FileResponse(matches3[0])
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
            response = FileResponse(matches4[0])
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
async def serve_generated_story_file(story_id: str, filename: str):
    """
    Serve files from generated_stories folder with proper CORS headers.
    Similar to saved-stories endpoint but for in-progress/temporary stories.
    Supports both old format (scene_0.png) and new format (uuid_scene_0.png).
    """
    import glob
    import os
    from pathlib import Path
    from fastapi.responses import FileResponse
    
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
        response = FileResponse(exact_path)
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
        response = FileResponse(matches1[0])
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
            response = FileResponse(old_format_path)
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
                response = FileResponse(matches3[0])
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
            response = FileResponse(matches4[0])
            response.headers["Access-Control-Allow-Origin"] = "https://edusmart.ign3el.com"
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            return response
    
    # List all files in directory for debugging
    all_files = list(story_dir.iterdir())
    logger.error(f"❌ File not found. Available files: {[f.name for f in all_files]}")
    raise HTTPException(status_code=404, detail=f"File not found: {filename}")

app.mount("/api/uploads", StaticFiles(directory="uploads"), name="uploads")

def _safe_story_dirname(story_name: str, story_id: str) -> str:
    """Create a filesystem-friendly folder name; fallback to ID fragment on collision/empty."""
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", story_name.strip().lower()) if story_name else ""
    slug = slug.strip("-") or f"story-{story_id[:8]}"
    if os.path.exists(os.path.join("saved_stories", slug)):
        slug = f"{slug}-{story_id[:8]}"
    return slug

@app.get("/api/avatars")
async def get_avatars() -> List[Dict[str, str]]:
    return [
        {"id": "wizard", "name": "Professor Paws", "description": "Wise teacher."},
        {"id": "robot", "name": "Robo-Buddy", "description": "Tech explorer."},
        {"id": "dinosaur", "name": "Dino-Explorer", "description": "Nature guide."}
    ]

async def generate_scene_media_progressive(story_id: str, scene_id: str, scene_index: int, scene: dict, voice: str, speed: float, story_seed: int, is_mobile: bool = False):
    """
    Generate image and audio for a scene IN PARALLEL with mobile optimization.
    Updates job state as each completes.
    """
    character_prompt = scene.get("image_prompt", "")
    text = scene.get("narrative_text", "")
    
    async def generate_image():
        try:
            if not character_prompt:
                job_manager.update_scene_image(scene_id, "skipped")
                return
            
            job_manager.update_scene_image(scene_id, "processing")
            
            # Use async generate_image method (now supports mobile optimization)
            img_bytes = await gemini.generate_image(
                prompt=character_prompt,
                scene_text=text,
                story_seed=story_seed,
                is_mobile=is_mobile
            )
            
            if img_bytes:
                img_name = f"scene_{scene_index}.png"
                # Use new storage manager
                try:
                    img_url = storage_manager.save_file(story_id, img_name, img_bytes, in_saved=False)
                    job_manager.update_scene_image(scene_id, "completed", img_url)
                    logger.info(f"✓ Scene {scene_index} image complete ({'mobile' if is_mobile else 'desktop'}): {img_url}")
                except Exception as save_error:
                    logger.error(f"Failed to save image: {save_error}")
                    job_manager.update_scene_image(scene_id, "failed")
            else:
                job_manager.update_scene_image(scene_id, "failed")
                logger.error(f"✗ Scene {scene_index} image failed")
        except Exception as e:
            logger.error(f"✗ Scene {scene_index} image error: {e}")
            job_manager.update_scene_image(scene_id, "failed")
    
    async def generate_audio():
        try:
            if not text:
                job_manager.update_scene_audio(scene_id, "skipped")
                return
            
            job_manager.update_scene_audio(scene_id, "processing")
            
            # Use mobile-optimized audio for mobile devices
            from services.chatterbox_client import chatterbox
            if is_mobile:
                audio_bytes = await chatterbox.generate_audio_optimized(text, voice, True)
            else:
                audio_bytes = await kokoro_tts.generate_audio(text, voice, speed)
            
            if audio_bytes:
                aud_name = f"scene_{scene_index}.wav"  # Kokoro provides WAV audio
                # Use new storage manager
                try:
                    aud_url = storage_manager.save_file(story_id, aud_name, audio_bytes, in_saved=False)
                    job_manager.update_scene_audio(scene_id, "completed", aud_url)
                    logger.info(f"✓ Scene {scene_index} audio complete ({'mobile' if is_mobile else 'desktop'}): {aud_url}")
                except Exception as save_error:
                    logger.error(f"Failed to save audio: {save_error}")
                    job_manager.update_scene_audio(scene_id, "failed")
            else:
                job_manager.update_scene_audio(scene_id, "failed")
                logger.error(f"✗ Scene {scene_index} audio failed")
        except Exception as e:
            logger.error(f"✗ Scene {scene_index} audio error: {e}")
            job_manager.update_scene_audio(scene_id, "failed")
    
    # Run image and audio generation IN PARALLEL
    await asyncio.gather(generate_image(), generate_audio())

async def generate_remaining_tts(story_id: str, scenes: list, scene_ids: list, voice: str, storage_manager, job_manager):
    """Generate TTS for scenes 1-9 in background using progressive batching"""
    try:
        from services.chatterbox_client import chatterbox
        
        # Use gemini's progressive TTS method
        await gemini.generate_progressive_tts(
            story_id=story_id,
            scenes=scenes,
            batch_size=2,
            max_threads_per_tts=1
        )
        
        # Update job manager for each completed scene
        for i, scene_id in enumerate(scene_ids):
            scene_num = i + 1  # +1 because Scene 0 is already done
            cache_file = f"outputs/audio_cache/audio_{story_id}_{scene_num}.mp3"
            
            if os.path.exists(cache_file):
                # Save to story folder
                with open(cache_file, 'rb') as f:
                    audio_bytes = f.read()
                
                aud_name = f"scene_{scene_num}.wav"
                try:
                    aud_url = storage_manager.save_file(story_id, aud_name, audio_bytes, in_saved=False)
                    job_manager.update_scene_audio(scene_id, "completed", aud_url)
                    logger.info(f"✓ Scene {scene_num} audio completed")
                except Exception as e:
                    logger.error(f"✗ Failed to save scene {scene_num} audio: {e}")
                    job_manager.update_scene_audio(scene_id, "failed")
            else:
                job_manager.update_scene_audio(scene_id, "failed")
                logger.error(f"✗ Scene {scene_num} audio not found in cache")
        
        logger.info(f"✓ All TTS generation complete for story {story_id}")
        
    except Exception as e:
        logger.error(f"✗ Background TTS generation error: {e}")

async def run_ai_workflow_progressive_mobile(story_id: str, file_path: str, grade_level: str, voice: str, speed: float, is_mobile: bool):
    """
    Progressive story generation workflow:
    1. Create story folder in generated_stories
    2. Generate story structure
    3. Create scene records immediately
    4. Process scenes in parallel (images + audio per scene)
    """
    try:
        # Create story folder with metadata
        storage_manager.create_story_folder(story_id, {
            "grade_level": grade_level,
            "voice": voice,
            "speed": speed,
            "file_path": file_path
        })
        logger.info(f"📁 Created story folder: generated_stories/{story_id}")
        
        # Generate story structure
        story_data = await asyncio.to_thread(gemini.process_file_to_story, file_path, grade_level)
        
        if not story_data:
            raise Exception("AI failed to generate story content.")
        
        scenes = story_data.get("scenes", [])
        title = story_data.get("title", "Untitled Story")
        quiz = story_data.get("quiz", [])
        
        # Initialize job state with quiz data
        job_manager.update_story_metadata(story_id, title, len(scenes), quiz)
        
        # Create scene records immediately (text is ready)
        scene_ids = []
        for i, scene in enumerate(scenes):
            scene_id = job_manager.create_scene(
                story_id, 
                i, 
                scene.get("narrative_text", ""),
                scene.get("image_prompt", "")
            )
            scene_ids.append(scene_id)
        
        logger.info(f"✓ Story structure ready: {len(scenes)} scenes")
        
        # --- Optimized Generation Strategy ---
        story_seed = int(uuid.uuid4().hex[:8], 16)
        force_mobile = True
        
        # Define background task for remaining images (Scenes 1..N)
        async def generate_remaining_images_background(r_scenes, seed, mobile):
            logger.info(f"🎨 Background generating images for {len(r_scenes)} scenes...")
            try:
                images_map = await gemini.generate_images_parallel(
                    r_scenes,
                    seed,
                    max_workers=4,
                    is_mobile=mobile,
                    start_index=1
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
                scene_num=0
            )
            
            # 🔗 CRITICAL: Trigger background generation IMMEDIATELY after Scene 0 Image finishes
            # This prevents RunPod 10s idle timeout if TTS takes longer than Image gen
            if len(scenes) > 1:
                logger.info("🔗 Chaining background image generation immediately...")
                asyncio.create_task(
                    generate_remaining_images_background(scenes[1:], story_seed, force_mobile)
                )
                
            return img_0

        logger.info("🚀 Starting parallel Scene 0 Image + Scene 0 TTS generation...")

        # 1. Generate Scene 0 Image (and chain background)
        scene_0_image_task = generate_scene_0_image_and_chain()
        
        # 2. Generate Scene 0 TTS
        from services.chatterbox_client import chatterbox
        scene_0_tts_task = chatterbox.generate_audio(
            text=scenes[0]['narrative_text'],
            voice=voice
        )
        
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
            aud_name = "scene_0.wav"
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
            asyncio.create_task(
                generate_remaining_tts(story_id, scenes[1:], scene_ids[1:], voice, storage_manager, job_manager)
            )
        
        # ✅ CRITICAL FIX: Mark story as completed so frontend can display Scene 0 immediately
        # Note: JobStateManager tracks completion via scene statuses, not a separate status field
        # The story is considered complete when all scenes have images
        logger.info(f"✅ Story {story_id} marked as ready for display")
        
    except Exception as e:
        logger.error(f"AI Workflow Error: {e}")
        job_manager.mark_story_failed(story_id, str(e))

@app.post("/api/check-duplicate")
async def check_duplicate(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)
):
    """Check if a file has been uploaded across both saved and generated stories."""
    # Read file content
    file_content = await file.read()
    
    # Use hash service to find duplicates
    duplicate_info = hash_service.find_duplicate(file_content, file.filename)
    
    # Reset file pointer for potential reuse
    await file.seek(0)
    
    if duplicate_info:
        # Check if duplicate is in saved_stories (persistent) or generated_stories (temp)
        saved_matches = duplicate_info.get("saved_stories", [])
        generated_matches = duplicate_info.get("generated_stories", [])
        
        # Prefer saved stories as they are permanent
        if saved_matches:
            # Get the most recent saved story from database
            try:
                with get_db_cursor() as cursor:
                    # Get all matching story IDs and sort by created_at DESC to get most recent
                    story_ids = [m["story_id"] for m in saved_matches]
                    placeholders = ','.join(['%s'] * len(story_ids))
                    
                    cursor.execute(f"""
                        SELECT story_id, name, created_at, user_id
                        FROM user_stories
                        WHERE story_id IN ({placeholders})
                        AND user_id = %s
                        ORDER BY created_at DESC
                        LIMIT 1
                    """, (*story_ids, current_user["id"]))
                    
                    db_story = cursor.fetchone()
                    
                    if db_story:
                        return {
                            "is_duplicate": True,
                            "duplicate_type": "saved",
                            "story_id": db_story["story_id"],
                            "story_title": db_story["name"],
                            "created_at": db_story["created_at"].isoformat() if db_story["created_at"] else None,
                            "created_by": current_user["username"],
                            "file_hash": duplicate_info["hash"]
                        }
            except Exception as e:
                logger.error(f"Error fetching duplicate story from DB: {e}")
                # Fallback to hash service metadata
                pass
            
            # Fallback if DB query fails
            match = saved_matches[0]
            metadata = match.get("metadata", {})
            return {
                "is_duplicate": True,
                "duplicate_type": "saved",
                "story_id": match["story_id"],
                "story_title": metadata.get("title", metadata.get("original_filename", "Unknown")),
                "created_at": metadata.get("created_timestamp", None),
                "created_by": current_user["username"],
                "file_hash": duplicate_info["hash"]
            }
        elif generated_matches:
            # For generated stories, use job manager to get most recent
            match = generated_matches[-1]  # Get most recent (last in list)
            story_id = match["story_id"]
            
            logger.info(f"Duplicate found in generated stories: {story_id}")
            
            try:
                status = job_manager.get_job_status(story_id)
                logger.info(f"Job status for {story_id}: {status}")
                
                # Extract title from status
                title = status.get("title", "Unknown")
                created_at = status.get("created_at", None)
                
                # If no created_at in status, try to get from metadata
                if not created_at:
                    metadata = match.get("metadata", {})
                    created_at = metadata.get("created_timestamp", None)
                
                response = {
                    "is_duplicate": True,
                    "duplicate_type": "generated",
                    "story_id": story_id,
                    "story_title": title,
                    "created_at": created_at,
                    "created_by": current_user["username"],
                    "file_hash": duplicate_info["hash"]
                }
                logger.info(f"Returning duplicate response: {response}")
                return response
                
            except Exception as e:
                logger.error(f"Error fetching generated story status: {e}")
                # Fallback
                metadata = match.get("metadata", {})
                response = {
                    "is_duplicate": True,
                    "duplicate_type": "generated",
                    "story_id": story_id,
                    "story_title": metadata.get("title", "Unknown"),
                    "created_at": metadata.get("created_timestamp", None),
                    "created_by": current_user["username"],
                    "file_hash": duplicate_info["hash"]
                }
                logger.info(f"Returning fallback duplicate response: {response}")
                return response
    
    return {"is_duplicate": False, "file_hash": hash_service.generate_bytes_hash(file_content)}

@app.post("/api/upload")
async def upload_story(
    background_tasks: BackgroundTasksExplicit,  # type: ignore
    file: UploadFile = File(...), 
    grade_level: str = Form("Grade 4"), 
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
    
    # Calculate hash if not provided
    if not file_hash:
        file_hash = hash_service.generate_bytes_hash(file_content)
    
    # Check for duplicate unless force_new is True
    duplicate_info = None
    if not force_new:
        duplicate_info = hash_service.find_duplicate(file_content, file.filename)
    
    if duplicate_info:
        saved_matches = duplicate_info.get("saved_stories", [])
        generated_matches = duplicate_info.get("generated_stories", [])
        
        # Return duplicate info - frontend will handle user choice
        if saved_matches or generated_matches:
            match = (saved_matches[0] if saved_matches else generated_matches[0])
            duplicate_type = "saved" if saved_matches else "generated"
            
            # Extract metadata
            metadata = match.get("metadata", {})
            return {
                "is_duplicate": True,
                "duplicate_type": duplicate_type,
                "story_id": match["story_id"],
                "story_title": metadata.get("title", metadata.get("original_filename", "Unknown")),
                "created_at": metadata.get("created_timestamp", "Unknown date"),
                "created_by": metadata.get("username", "Unknown"),
                "file_hash": file_hash,
                "message": "File already exists. Choose to view existing or generate new."
            }
    
    # No duplicate or force_new=True - create new story
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
    temp_file_path = os.path.join(temp_dir, filename)
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
        username=current_user['username']
    )
    
    # Detect if user is on mobile device
    is_mobile = False
    if user_agent:
        mobile_keywords = ['mobile', 'android', 'iphone', 'ipad', 'windows phone', 'blackberry']
        is_mobile = any(keyword in user_agent.lower() for keyword in mobile_keywords)
    
    # Use progressive workflow with mobile optimization
    background_tasks.add_task(
        run_ai_workflow_progressive_mobile, 
        story_id, 
        temp_file_path, 
        grade_level, 
        voice, 
        speed, 
        is_mobile
    )
    
    return {
        "job_id": story_id, 
        "is_mobile": is_mobile,
        "message": "Story generation started"
    }

@app.post("/api/handle-duplicate-choice")
async def handle_duplicate_choice(
    background_tasks: BackgroundTasks,
    choice: str = Form(...),  # "view_existing" or "generate_new"
    duplicate_story_id: str = Form(...),
    duplicate_type: str = Form(...),  # "saved" or "generated"
    file: UploadFile = File(...),
    grade_level: str = Form("Grade 4"),
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
        # Create new story with fresh UUID
        new_story_id = str(uuid.uuid4())
        
        # Read file content
        file_content = await file.read()
        
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
        temp_file_path = os.path.join(temp_dir, filename)
        with open(temp_file_path, "wb") as f:
            f.write(file_content)
        
        # Initialize job state
        job_manager.initialize_story(
            new_story_id, 
            grade_level, 
            file_hash=hash_service.generate_bytes_hash(file_content), 
            user_id=current_user['id'], 
            username=current_user['username']
        )
        
        # Detect mobile
        is_mobile = False
        if user_agent:
            mobile_keywords = ['mobile', 'android', 'iphone', 'ipad', 'windows phone', 'blackberry']
            is_mobile = any(keyword in user_agent.lower() for keyword in mobile_keywords)
        
        # Start generation
        background_tasks.add_task(
            run_ai_workflow_progressive_mobile, 
            new_story_id, 
            temp_file_path, 
            grade_level, 
            voice, 
            speed, 
            is_mobile
        )
        
        return {
            "action": "generate_new",
            "job_id": new_story_id,
            "message": "New story generation started"
        }
    
    else:
        raise HTTPException(status_code=400, detail="Invalid choice")


# Progressive endpoints for scene-by-scene loading
@app.get("/api/story/{story_id}/status")
async def get_story_status(story_id: str, current_user: dict = Depends(get_current_user)) -> Dict[str, Any]:
    """Get overall story status with scene completion info."""
    logger.info(f"🔍 Getting story status for: {story_id}")
    
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
async def get_scene_status(story_id: str, scene_index: int) -> Dict[str, Any]:
    """Get specific scene data."""
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


@app.get("/api/status/{job_id}")
async def get_status(job_id: str, current_user: dict = Depends(get_current_user)) -> Dict[str, Any]:
    # Check if it's a progressive story
    status = job_manager.get_story_status(job_id)
    if status:
        scenes = job_manager.get_all_scenes(job_id)
        # Filter to only include completed scenes (both image and audio ready)
        completed_scenes = [
            {
                "text": s["text"],
                "image_url": s["image_url"],
                "audio_url": s["audio_url"]
            }
            for s in scenes
            if s["image_status"] == "completed" and s["audio_status"] == "completed"
        ]
        
        # Calculate real progress based on completed scenes
        actual_progress = int((len(completed_scenes) / status["total_scenes"]) * 100) if status["total_scenes"] > 0 else 0
        
        # Parse quiz data if it's stored as JSON string
        quiz_data = status.get("quiz", [])
        if isinstance(quiz_data, str):
            try:
                quiz_data = json.loads(quiz_data)
            except json.JSONDecodeError:
                quiz_data = []
        
        return {
            "status": status["status"],
            "progress": actual_progress,
            "total_scenes": status["total_scenes"],  # Always include total count
            "completed_scene_count": len(completed_scenes),  # Number of completed scenes
            "result": {
                "title": status["title"],
                "scenes": completed_scenes,
                "quiz": quiz_data
            }
        }
    
    # Fall back to old job system
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return jobs[job_id]

@app.post("/api/save-story/{job_id}")
async def save_story(job_id: str, story_name: str = Form(...), user: User = Depends(get_current_user)):
    """
    Saves a generated story to the database, associating it with the current user.
    This also moves the story's assets from temporary storage to permanent storage.
    """
    # Check if it's a progressive story
    status = job_manager.get_story_status(job_id)
    if status and status["status"] == "completed":
        # Progressive story system - move folder from generated_stories to saved_stories
        saved_story_id = str(uuid.uuid4())
        scenes = job_manager.get_all_scenes(job_id)
        
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
                "text": s["text"],
                "image_url": s.get("image_url", ""),
                "audio_url": s.get("audio_url", "")
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
        
        story_data = {
            "title": status["title"],
            "scenes": updated_scenes,
            "quiz": status.get("quiz", [])
        }
        
        success = StoryOperations.save_story(
            user_id=user['id'],
            story_id=saved_story_id,
            name=story_name,
            story_data=story_data
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
            story_data=story_data
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
    story = StoryOperations.get_story(story_id, user)
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
            # Fix 2: Relative path (e.g. "scene_0.wav") -> prepend full API path
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
            scene_data = {
                "text": scene["text"],
                "image_url": f"scene_{idx}.png",
                "audio_url": f"scene_{idx}.wav"
            }
            story_data["scenes"].append(scene_data)
            
            # Add image file
            if scene["image_url"]:
                img_path = scene["image_url"].replace("/api/outputs/", "outputs/")
                if os.path.exists(img_path):
                    zip_file.write(img_path, f"scene_{idx}.png")
            
            # Add audio file
            if scene["audio_url"]:
                audio_path = scene["audio_url"].replace("/api/outputs/", "outputs/")
                if os.path.exists(audio_path):
                    zip_file.write(audio_path, f"scene_{idx}.wav")
        
        # Add story.json
        zip_file.writestr("story.json", json.dumps(story_data, indent=2))
    
    zip_buffer.seek(0)
    
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={status['title'].replace(' ', '_')}.zip"
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
                "audio_url": f"scene_{idx}.wav"
            }
            export_data["scenes"].append(scene_data)
            
            # Add image file from saved_stories directory
            if scene.get("image_url"):
                img_path = os.path.join("saved_stories", story_id, f"scene_{idx}.png")
                if os.path.exists(img_path):
                    zip_file.write(img_path, f"scene_{idx}.png")
            
            # Add audio file from saved_stories directory
            if scene.get("audio_url"):
                audio_path = os.path.join("saved_stories", story_id, f"scene_{idx}.wav")
                if os.path.exists(audio_path):
                    zip_file.write(audio_path, f"scene_{idx}.wav")
        
        # Add story.json
        zip_file.writestr("story.json", json.dumps(export_data, indent=2))
    
    zip_buffer.seek(0)
    
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={story['name'].replace(' ', '_')}.zip"
        }
    )

# --- PROGRESSIVE TTS ENDPOINTS ---

@app.get("/api/story/{story_id}/tts-status")
async def get_tts_status(story_id: str):
    """Get specialized progressive TTS generation status"""
    # Use gemini service's status tracking
    return await gemini.get_tts_status(story_id)

@app.get("/api/story/{story_id}/scene/{scene_num}/audio")
async def get_scene_audio(story_id: str, scene_num: int, current_user: dict = Depends(get_current_user)):
    """Get scene audio (from cache or generated/saved folder) with waterfall fallbacks"""
    import os
    import aiofiles
    from fastapi.responses import FileResponse, Response
    
    # 1. Check progressive TTS cache (fastest)
    # Note: scene_num matches the 1-based index used in file names
    cache_file = f"outputs/audio_cache/audio_{story_id}_{scene_num}.mp3"
    
    if os.path.exists(cache_file):
        return FileResponse(cache_file, media_type="audio/mpeg")
    
    # 2. Check active job (in-memory/generated_stories)
    try:
        story_dir = storage_manager.get_story_path(story_id, in_saved=False)
        
        # Try WAV (Kokoro default) and MP3
        for ext in [".wav", ".mp3"]:
            # Try simple name
            simple_path = os.path.join(story_dir, f"scene_{scene_num}{ext}")
            if os.path.exists(simple_path):
                media_type = "audio/wav" if ext == ".wav" else "audio/mpeg"
                return FileResponse(simple_path, media_type=media_type)
            
            # Try UUID prefixed
            import glob
            matches = glob.glob(os.path.join(story_dir, f"*_scene_{scene_num}{ext}"))
            if matches:
                 media_type = "audio/wav" if ext == ".wav" else "audio/mpeg"
                 return FileResponse(matches[0], media_type=media_type)
    except Exception:
        pass
        
    # 3. Check saved stories (persistent storage)
    try:
        story_dir = storage_manager.get_story_path(story_id, in_saved=True)
         # Try WAV (Kokoro default) and MP3
        for ext in [".wav", ".mp3"]:
            # Try simple name
            simple_path = os.path.join(story_dir, f"scene_{scene_num}{ext}")
            if os.path.exists(simple_path):
                media_type = "audio/wav" if ext == ".wav" else "audio/mpeg"
                return FileResponse(simple_path, media_type=media_type)
            
            # Try UUID prefixed
            import glob
            matches = glob.glob(os.path.join(story_dir, f"*_scene_{scene_num}{ext}"))
            if matches:
                 media_type = "audio/wav" if ext == ".wav" else "audio/mpeg"
                 return FileResponse(matches[0], media_type=media_type)
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
            # Update quiz_completed status
            cursor.execute("""
                UPDATE user_stories
                SET quiz_completed = TRUE
                WHERE user_id = %s AND story_id = %s
            """, (current_user["id"], story_id))
            
            if cursor.rowcount == 0:
                raise HTTPException(
                    status_code=404,
                    detail="Story not found or not owned by user"
                )
            
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


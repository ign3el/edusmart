import logging
import sqlite3
import requests
import os
import json
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import io

from .auth import get_current_user
from database_models import User, StoryOperations
from database import get_db_cursor
import mysql.connector
from services.kokoro_client import generate_tts

# Setup logging
logger = logging.getLogger(__name__)

# Create a new router for admin endpoints
router = APIRouter(
    prefix="/api/admin",
    tags=["Admin"],
    responses={403: {"description": "Operation not permitted"}},
)

# --- Pydantic Models ---
class TtsTestRequest(BaseModel):
    text: str
    voice: str = "af_sarah"
    speed: float = 1.0

# --- Dependency for Admin User ---
async def get_admin_user(current_user: User = Depends(get_current_user)):
    """
    Dependency that checks if the current user is an admin.
    If not, it raises a 403 Forbidden error.
    """
    if not current_user or not current_user.get('is_admin'):
        raise HTTPException(status_code=403, detail="You do not have permission to access this resource.")
    return current_user

# --- TTS Test Endpoint ---
@router.post("/tts/test", dependencies=[Depends(get_admin_user)])
def test_tts(request: TtsTestRequest):
    """
    Allows admins to test the Kokoro TTS service with custom text and settings.
    Streams back a WAV audio file.
    """
    try:
        if request.voice == "ar_teacher":
            from config import Config
            # Route to Piper TTS (Secure External Endpoint) for Arabic
            piper_url = Config.TTS_API_URL.rsplit("/v1", 1)[0] + "/tts"
            headers = {"TTS_API_KEY": Config.TTS_API_KEY}
            # Piper typically accepts GET requests with text param
            resp = requests.get(piper_url, params={"text": request.text}, headers=headers, timeout=10)
            
            if resp.status_code != 200:
                raise Exception(f"Piper TTS Error {resp.status_code}: {resp.text}")
            audio_bytes = resp.content
        else:
            # Route to Kokoro TTS (Port 8880) for others
            audio_bytes = generate_tts(
                text=request.text,
                voice=request.voice,
                speed=request.speed
            )
            
        if not audio_bytes:
            raise HTTPException(status_code=500, detail="TTS generation failed, received no audio data.")

        return StreamingResponse(io.BytesIO(audio_bytes), media_type="audio/wav")
    except Exception as e:
        logger.error(f"Error in TTS test endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- Database Viewer for job_state.db ---
DB_PATH = "job_state.db"

def get_db_connection():
    """Establishes a connection to the SQLite database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        logger.error(f"Database connection failed: {e}")
        raise HTTPException(status_code=500, detail="Database connection failed.")

@router.get("/db/job_state/tables", dependencies=[Depends(get_admin_user)])
async def get_job_state_db_tables():
    """
    Returns a list of table names from the job_state.db SQLite database.
    Only accessible by admin users.
    """
    tables = []
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
        rows = cursor.fetchall()
        tables = [row['name'] for row in rows]
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Failed to query database tables: {e}")
    finally:
        if conn:
            conn.close()
    return {"tables": tables}

@router.get("/db/job_state/table/{table_name}", dependencies=[Depends(get_admin_user)])
async def get_job_state_db_table_content(table_name: str):
    """
    Returns the content of a specified table from the job_state.db SQLite database.
    Performs a security check on the table name to prevent SQL injection.
    Only accessible by admin users.
    """
    # Security First: Validate table_name against a fetched list of actual tables
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        valid_tables = [row['name'] for row in cursor.fetchall()]
        
        if table_name not in valid_tables:
            raise HTTPException(status_code=400, detail=f"Invalid table name: {table_name}")

        # Now that the table name is validated, it's safe to use it in the query
        cursor.execute(f"SELECT * FROM {table_name} ORDER BY created_at DESC;")
        rows = cursor.fetchall()
        content = [dict(row) for row in rows]
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Failed to query table content: {e}")
    finally:
        if conn:
            conn.close()
    return {"table_name": table_name, "content": content}

@router.post("/migrate-saved-stories", dependencies=[Depends(get_admin_user)])
async def migrate_saved_stories():
    """
    Admin endpoint to scan saved_stories folder and create database entries
    for stories that exist on disk but not in the database.
    """
    # Try multiple possible locations
    possible_paths = [
        Path("saved_stories"),
        Path("backend/saved_stories"),
        Path("/app/saved_stories"),
        Path("/app/backend/saved_stories")
    ]
    
    saved_stories_path = None
    for path in possible_paths:
        if path.exists():
            saved_stories_path = path
            break
    
    if not saved_stories_path:
        return {
            "success": False,
            "message": "saved_stories folder not found",
            "searched_paths": [str(p.absolute()) for p in possible_paths]
        }
    
    story_folders = [f for f in saved_stories_path.iterdir() if f.is_dir()]
    
    # Get admin user ID
    admin_user_id = None
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute("SELECT id FROM users WHERE is_admin = 1 LIMIT 1")
            admin = cursor.fetchone()
            if admin:
                admin_user_id = admin['id']
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to get admin user: {str(e)}"
        }
    
    if not admin_user_id:
        return {
            "success": False,
            "message": "No admin user found in database"
        }
    
    migrated = []
    skipped = []
    errors = []
    
    for story_folder in story_folders:
        folder_name = story_folder.name
        metadata_path = story_folder / "metadata.json"
        
        if not metadata_path.exists():
            skipped.append({"story_id": folder_name, "reason": "No metadata.json found"})
            continue
        
        try:
            # Read metadata.json
            with open(metadata_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
            
            story_id = metadata.get('id', folder_name)
            story_name = metadata.get('name', 'Untitled Story')
            story_data = metadata.get('story_data', {})
            story_title = story_data.get('title', story_name)
            
            with get_db_cursor(commit=True) as cursor:
                cursor.execute("SELECT story_id FROM user_stories WHERE story_id = %s", (story_id,))
                existing = cursor.fetchone()
                
                if existing:
                    skipped.append({"story_id": story_id, "reason": "Already in database"})
                    continue
                
                # Insert into database assigned to admin
                query = """
                    INSERT INTO user_stories (story_id, user_id, name, story_data, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """
                
                now = datetime.now()
                created_at = datetime.fromtimestamp(story_folder.stat().st_ctime)
                
                cursor.execute(query, (
                    story_id,
                    admin_user_id,  # Assign to admin instead of NULL
                    story_name,
                    json.dumps(story_data),
                    created_at,
                    now
                ))
                
                migrated.append({"story_id": story_id, "title": story_name})
                
        except mysql.connector.Error as e:
            errors.append({"story_id": story_id, "error": str(e)})
        except json.JSONDecodeError as e:
            errors.append({"story_id": story_id, "error": f"Invalid JSON: {e}"})
        except Exception as e:
            errors.append({"story_id": story_id, "error": str(e)})
    
    return {
        "success": True,
        "saved_stories_path": str(saved_stories_path.absolute()),
        "total_folders": len(story_folders),
        "migrated_count": len(migrated),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "migrated": migrated,
        "skipped": skipped,
        "errors": errors
    }

@router.get("/debug-stories", dependencies=[Depends(get_admin_user)])
async def debug_stories():
    """
    Debug endpoint to check what stories exist in database vs saved_stories folder.
    """
    # Check database
    try:
        with get_db_cursor() as cursor:
            cursor.execute("SELECT story_id, name, user_id FROM user_stories ORDER BY updated_at DESC")
            db_stories = cursor.fetchall()
    except Exception as e:
        db_stories = []
        db_error = str(e)
    
    # Check saved_stories folder
    possible_paths = [
        Path("saved_stories"),
        Path("backend/saved_stories"),
        Path("/app/saved_stories"),
        Path("/app/backend/saved_stories")
    ]
    
    saved_stories_path = None
    for path in possible_paths:
        if path.exists():
            saved_stories_path = path
            break
    
    disk_stories = []
    if saved_stories_path:
        for folder in saved_stories_path.iterdir():
            if folder.is_dir():
                story_json = folder / "story.json"
                disk_stories.append({
                    "story_id": folder.name,
                    "has_json": story_json.exists()
                })
    
    return {
        "database": {
            "count": len(db_stories),
            "stories": [{"story_id": s["story_id"], "name": s["name"], "user_id": s.get("user_id")} for s in db_stories]
        },
        "disk": {
            "path": str(saved_stories_path.absolute()) if saved_stories_path else None,
            "count": len(disk_stories),
            "stories": disk_stories
        }
    }

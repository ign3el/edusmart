import asyncio
import logging
import sqlite3
import os
import json
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field, validator
import io
from typing import Optional

from .auth import get_current_user
from database_models import User, StoryOperations, UserOperations
from database import get_db_cursor
from auth import get_password_hash
import mysql.connector
from services.kokoro_client import generate_tts
from routers.billing import refund_credit

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

class UserUpdateRequest(BaseModel):
    is_admin: Optional[bool] = None
    is_verified: Optional[bool] = None
    is_premium: Optional[bool] = None
    username: Optional[str] = None
    email: Optional[EmailStr] = None
    password: Optional[str] = None

class UserCreateRequest(BaseModel):
    email: EmailStr
    username: str
    password: str
    is_admin: bool = False
    is_verified: bool = True

class StoryUpdateRequest(BaseModel):
    title: Optional[str] = None

class PlanUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    price_display: Optional[str] = None
    stripe_price_id: Optional[str] = None
    credits_included: Optional[int] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None
    description: Optional[str] = None
    features: Optional[str] = None
    is_recommended: Optional[bool] = None

class PromoCodeCreateRequest(BaseModel):
    code: str = Field(..., min_length=3, max_length=50)
    discount_type: str
    discount_value: int
    stripe_coupon_id: Optional[str] = None
    applies_to_tier: Optional[str] = None
    max_redemptions: Optional[int] = None
    max_redemptions_per_user: int = 1
    expires_at: Optional[datetime] = None

    @validator('discount_type')
    def valid_discount_type(cls, v):
        if v not in ('percent_off', 'free_credits'):
            raise ValueError("discount_type must be 'percent_off' or 'free_credits'")
        return v

    @validator('discount_value')
    def valid_discount_value(cls, v, values):
        if v <= 0:
            raise ValueError('discount_value must be positive')
        if values.get('discount_type') == 'percent_off' and v > 100:
            raise ValueError('percent_off discount_value cannot exceed 100')
        return v

class PromoCodeUpdateRequest(BaseModel):
    discount_value: Optional[int] = None
    stripe_coupon_id: Optional[str] = None
    max_redemptions: Optional[int] = None
    max_redemptions_per_user: Optional[int] = None
    expires_at: Optional[datetime] = None
    is_active: Optional[bool] = None

class GrantCreditsRequest(BaseModel):
    amount: int
    note: Optional[str] = None

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
async def test_tts(request: TtsTestRequest):
    """
    Allows admins to test the TTS service actually used in production
    (Kokoro for standard voices, Piper for "ar_teacher") with custom text
    and settings. Streams back a WAV audio file.
    """
    try:
        if request.voice == "ar_teacher":
            # Same client the real generation pipeline uses - keeps this test
            # tool honest about what production actually does.
            from services.piper_client import piper_tts
            audio_bytes = await piper_tts.generate_audio(request.text, speed=request.speed, silence=0.3)
        else:
            # Route to Kokoro TTS (Port 8880) for others
            audio_bytes = await asyncio.to_thread(
                generate_tts,
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
# Must match job_state.py's JobStateManager default path - this used to
# point at "job_state.db" (a stale, empty file that happened to sit in the
# source tree) instead of the actual live database job_state.py writes to.
DB_PATH = "db_data/job_state.db"

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

@router.get("/users", dependencies=[Depends(get_admin_user)])
async def list_users():
    """List all users with their story counts"""
    try:
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT 
                    u.id,
                    u.email,
                    u.username,
                    u.created_at,
                    u.is_admin,
                    u.is_verified,
                    u.subscription_tier,
                    u.credits_balance,
                    COUNT(us.story_id) as story_count
                FROM users u
                LEFT JOIN user_stories us ON u.id = us.user_id
                GROUP BY u.id
                ORDER BY u.created_at DESC
            """)
            users = cursor.fetchall()
        
        return {
            "success": True,
            "users": [dict(u) for u in users]
        }
    except Exception as e:
        logger.error(f"List users failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/users/{user_id}", dependencies=[Depends(get_admin_user)])
async def get_user_details(user_id: int):
    """Get detailed user info including their stories"""
    try:
        with get_db_cursor() as cursor:
            # Get user info (explicit columns - never return password_hash)
            cursor.execute(
                """
                SELECT id, email, username, created_at, updated_at, is_admin, is_verified, is_premium,
                       subscription_tier, subscription_status, credits_balance
                FROM users WHERE id = %s
                """,
                (user_id,)
            )
            user = cursor.fetchone()
            
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            
            # Get user's stories
            cursor.execute("""
                SELECT story_id, name, created_at, updated_at
                FROM user_stories
                WHERE user_id = %s
                ORDER BY created_at DESC
            """, (user_id,))
            stories = cursor.fetchall()
            
            return {
                "success": True,
                "user": dict(user),
                "stories": [dict(s) for s in stories]
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get user details failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/users/{user_id}", dependencies=[Depends(get_admin_user)])
async def update_user(user_id: int, update: UserUpdateRequest):
    """Update user fields: status flags, username/email, or password."""
    try:
        updates = []
        values = []

        if update.is_admin is not None:
            updates.append("is_admin = %s")
            values.append(update.is_admin)
        if update.is_verified is not None:
            updates.append("is_verified = %s")
            values.append(update.is_verified)
        if update.is_premium is not None:
            updates.append("is_premium = %s")
            values.append(update.is_premium)
        if update.username is not None:
            updates.append("username = %s")
            values.append(update.username)
        if update.email is not None:
            updates.append("email = %s")
            values.append(update.email.lower())
        if update.password is not None:
            updates.append("password_hash = %s")
            values.append(get_password_hash(update.password))

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        values.append(user_id)

        try:
            with get_db_cursor(commit=True) as cursor:
                if update.is_admin is False:
                    cursor.execute("SELECT is_admin FROM users WHERE id = %s", (user_id,))
                    target = cursor.fetchone()
                    if target and target["is_admin"]:
                        cursor.execute("SELECT COUNT(*) as admin_count FROM users WHERE is_admin = 1")
                        if cursor.fetchone()["admin_count"] <= 1:
                            raise HTTPException(
                                status_code=400,
                                detail="Cannot remove admin rights from the last remaining admin account."
                            )

                cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = %s", values)

                if cursor.rowcount == 0:
                    raise HTTPException(status_code=404, detail="User not found")
        except mysql.connector.IntegrityError:
            raise HTTPException(status_code=409, detail="That username or email is already taken.")

        return {"success": True, "message": "User updated"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update user failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/users", dependencies=[Depends(get_admin_user)])
async def create_user(payload: UserCreateRequest):
    """Admin-created user account. Defaults to verified since an admin is vouching for it."""
    try:
        if UserOperations.get_by_email(payload.email) or UserOperations.get_by_username(payload.username):
            raise HTTPException(status_code=409, detail="An account with this email or username already exists.")

        user = UserOperations.create(
            email=payload.email,
            username=payload.username,
            password=payload.password
        )
        if not user:
            raise HTTPException(status_code=409, detail="An account with this email or username already exists.")

        flag_updates = []
        flag_values = []
        if payload.is_admin:
            flag_updates.append("is_admin = %s")
            flag_values.append(True)
        if payload.is_verified:
            flag_updates.append("is_verified = %s")
            flag_values.append(True)

        if flag_updates:
            flag_values.append(user["id"])
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(f"UPDATE users SET {', '.join(flag_updates)} WHERE id = %s", flag_values)
            user["is_admin"] = payload.is_admin
            user["is_verified"] = payload.is_verified

        return {"success": True, "user": user}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create user failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/users/{user_id}", dependencies=[Depends(get_admin_user)])
async def delete_user(user_id: int):
    """Delete user and all their stories"""
    try:
        with get_db_cursor(commit=True) as cursor:
            # Prevent deleting the last remaining admin - would lock everyone out
            cursor.execute("SELECT is_admin FROM users WHERE id = %s", (user_id,))
            target = cursor.fetchone()
            if target and target["is_admin"]:
                cursor.execute("SELECT COUNT(*) as admin_count FROM users WHERE is_admin = 1")
                if cursor.fetchone()["admin_count"] <= 1:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot delete the last remaining admin account."
                    )

            # Get user's stories first
            cursor.execute("SELECT story_id FROM user_stories WHERE user_id = %s", (user_id,))
            stories = cursor.fetchall()

            # Delete user's stories from database
            cursor.execute("DELETE FROM user_stories WHERE user_id = %s", (user_id,))

            # Delete user
            cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
            
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="User not found")
        
        # Delete story files from disk
        deleted_files = 0
        for story in stories:
            story_path = Path("saved_stories") / story["story_id"]
            if story_path.exists():
                import shutil
                shutil.rmtree(story_path)
                deleted_files += 1
        
        return {
            "success": True,
            "message": f"User and {deleted_files} stories deleted"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete user failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stories/all", dependencies=[Depends(get_admin_user)])
async def list_all_stories():
    """List ALL stories - both saved and unsaved (generated only)"""
    try:
        # Get saved stories from MySQL
        saved_stories = []
        try:
            with get_db_cursor() as cursor:
                cursor.execute("""
                    SELECT 
                        us.story_id,
                        us.name,
                        us.created_at,
                        us.updated_at,
                        us.user_id,
                        u.username,
                        u.email,
                        'saved' as story_type
                    FROM user_stories us
                    LEFT JOIN users u ON us.user_id = u.id
                    ORDER BY us.created_at DESC
                """)
                saved_stories = [dict(s) for s in cursor.fetchall()]
        except Exception as e:
            logger.warning(f"Could not fetch saved stories: {e}")
        
        # Get generated stories from SQLite
        generated_stories = []
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    story_id,
                    title,
                    status,
                    created_at,
                    updated_at,
                    username,
                    user_id,
                    total_scenes,
                    completed_scenes
                FROM stories
                ORDER BY created_at DESC
            """)
            
            for row in cursor.fetchall():
                # Check if this story is already in saved_stories
                is_saved = any(s["story_id"] == row["story_id"] for s in saved_stories)
                if not is_saved:
                    generated_stories.append({
                        "story_id": row["story_id"],
                        "name": row["title"] or "Untitled",
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                        "user_id": row["user_id"],
                        "username": row["username"],
                        "story_type": "generated",
                        "status": row["status"],
                        "total_scenes": row["total_scenes"],
                        "completed_scenes": row["completed_scenes"]
                    })
            
            conn.close()
        except Exception as e:
            logger.warning(f"Could not fetch generated stories: {e}")
        
        # Combine both
        all_stories = saved_stories + generated_stories
        
        # Sort by created_at descending. MySQL returns datetime objects, SQLite
        # returns strings for the same column - normalize to str for a safe compare.
        all_stories.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        
        return {
            "success": True,
            "total": len(all_stories),
            "saved_count": len(saved_stories),
            "generated_count": len(generated_stories),
            "stories": all_stories
        }
    except Exception as e:
        logger.error(f"List all stories failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/stories/{story_id}", dependencies=[Depends(get_admin_user)])
async def delete_story(story_id: str):
    """Delete a story (saved or generated)"""
    try:
        deleted_from = []
        
        # Try to delete from MySQL (saved stories)
        try:
            with get_db_cursor(commit=True) as cursor:
                cursor.execute("DELETE FROM user_stories WHERE story_id = %s", (story_id,))
                if cursor.rowcount > 0:
                    deleted_from.append("saved_stories")
        except Exception as e:
            logger.warning(f"Could not delete from saved_stories: {e}")
        
        # Try to delete from SQLite (generated stories)
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Delete scenes first
            cursor.execute("DELETE FROM scenes WHERE story_id = ?", (story_id,))
            scenes_deleted = cursor.rowcount
            
            # Delete story
            cursor.execute("DELETE FROM stories WHERE story_id = ?", (story_id,))
            if cursor.rowcount > 0:
                deleted_from.append("generated_stories")
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Could not delete from generated_stories: {e}")
        
        # Delete files from disk
        files_deleted = False
        story_path = Path("saved_stories") / story_id
        if story_path.exists():
            import shutil
            shutil.rmtree(story_path)
            files_deleted = True
        
        story_path2 = Path("outputs") / story_id
        if story_path2.exists():
            import shutil
            shutil.rmtree(story_path2)
            files_deleted = True
        
        if not deleted_from and not files_deleted:
            raise HTTPException(status_code=404, detail="Story not found")
        
        return {
            "success": True,
            "message": f"Story deleted from: {', '.join(deleted_from)}{' and files' if files_deleted else ''}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete story failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/stories/{story_id}/cancel", dependencies=[Depends(get_admin_user)])
async def cancel_story(story_id: str):
    """Mark a stuck/hung generation job as failed and refund its credit."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT status, user_id FROM stories WHERE story_id = ?", (story_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="Story not found")
        if row["status"] != "processing":
            conn.close()
            raise HTTPException(status_code=400, detail=f"Story is not processing (status: {row['status']})")

        cursor.execute("""
            UPDATE stories SET status = 'failed', updated_at = CURRENT_TIMESTAMP
            WHERE story_id = ?
        """, (story_id,))
        conn.commit()
        user_id = row["user_id"]
        conn.close()

        if user_id:
            try:
                refund_credit(user_id, story_id)
            except Exception as refund_err:
                logger.warning(f"Could not refund credit for cancelled job {story_id}: {refund_err}")

        return {"success": True, "message": f"Story {story_id} cancelled"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Cancel story failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class RetryResult(BaseModel):
    scenes_queued: int
    message: str


def _retryable_scenes(story_id: str) -> list:
    """Scenes that failed (or silently never produced a file) and can be redone.

    Both halves are recoverable from what SQLite already stores: `text` drives
    TTS, and `character_prompt` is the image prompt (verified - it holds strings
    like "A 3D animated scene of ..."). What is NOT stored is the original
    story_seed and voice, so a retried scene uses the configured defaults and may
    differ slightly in style from its neighbours. That is the honest trade for
    not having to re-upload a document that no longer exists on disk - uploads/
    is emptied after processing.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT scene_id, scene_index, text, character_prompt, image_status, audio_status,
                  image_url, audio_url
           FROM scenes WHERE story_id = ? ORDER BY scene_index""",
        (story_id,)
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    out = []
    for r in rows:
        needs_image = r["image_status"] != "completed" or not r["image_url"]
        needs_audio = r["audio_status"] != "completed" or not r["audio_url"]
        if needs_image or needs_audio:
            r["needs_image"] = needs_image and bool(r["character_prompt"])
            r["needs_audio"] = needs_audio and bool(r["text"])
            if r["needs_image"] or r["needs_audio"]:
                out.append(r)
    return out


async def _run_retry(story_id: str, scenes: list) -> None:
    """Regenerate the failed halves of each scene, then settle the story status."""
    from services.story_service import StoryService
    from story_storage import storage_manager
    from job_state import job_manager

    service = StoryService()
    seed = int(os.getenv("RUNPOD_SEED", "20240601"))
    repaired = 0

    for scene in scenes:
        idx = scene["scene_index"]

        if scene.get("needs_image"):
            try:
                img = await service.generate_image(
                    scene["character_prompt"], story_seed=seed, scene_num=idx
                )
                if img:
                    url = storage_manager.save_file(story_id, f"scene_{idx}.png", img, in_saved=False)
                    job_manager.update_scene_image(scene["scene_id"], "completed", url)
                    repaired += 1
                else:
                    job_manager.update_scene_image(scene["scene_id"], "failed")
            except Exception as e:
                logger.error(f"Retry image failed for {story_id} scene {idx}: {e}")
                job_manager.update_scene_image(scene["scene_id"], "failed")

        if scene.get("needs_audio"):
            try:
                audio = await asyncio.to_thread(generate_tts, scene["text"])
                if audio:
                    url = storage_manager.save_file(story_id, f"scene_{idx}.mp3", audio, in_saved=False)
                    job_manager.update_scene_audio(scene["scene_id"], "completed", url)
                    repaired += 1
                else:
                    job_manager.update_scene_audio(scene["scene_id"], "failed")
            except Exception as e:
                logger.error(f"Retry audio failed for {story_id} scene {idx}: {e}")
                job_manager.update_scene_audio(scene["scene_id"], "failed")

    # Settle: completed only if nothing is outstanding any more, otherwise back to
    # failed so the admin can see it still needs attention rather than it sitting
    # in 'processing' forever.
    still_broken = _retryable_scenes(story_id)
    final = "failed" if still_broken else "completed"
    conn = get_db_connection()
    conn.execute(
        "UPDATE stories SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE story_id = ?",
        (final, story_id)
    )
    conn.commit()
    conn.close()
    logger.info(
        f"Retry finished for {story_id}: {repaired} asset(s) regenerated, "
        f"{len(still_broken)} scene(s) still incomplete -> status '{final}'"
    )


@router.post("/stories/{story_id}/retry", dependencies=[Depends(get_admin_user)])
async def retry_story(story_id: str):
    """Regenerate the failed images/audio of a failed job, in place.

    This is a repair, not a re-run: the source document is long gone, so the
    story text is never regenerated. No credit is charged - the user's credit was
    already refunded when the job failed.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM stories WHERE story_id = ?", (story_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Story not found")
    if row["status"] == "processing":
        raise HTTPException(status_code=400, detail="Story is already processing")

    scenes = _retryable_scenes(story_id)
    if not scenes:
        raise HTTPException(
            status_code=400,
            detail="Nothing to retry - this job has no recoverable scenes. "
                   "It failed before any scene text was written, so it can only be deleted."
        )

    conn = get_db_connection()
    conn.execute(
        "UPDATE stories SET status = 'processing', updated_at = CURRENT_TIMESTAMP WHERE story_id = ?",
        (story_id,)
    )
    conn.commit()
    conn.close()

    task = asyncio.create_task(_run_retry(story_id, scenes))
    task.add_done_callback(
        lambda t: logger.error(f"Retry task crashed for {story_id}: {t.exception()}") if t.exception() else None
    )

    return RetryResult(
        scenes_queued=len(scenes),
        message=f"Retrying {len(scenes)} scene(s). Refresh in a moment to see progress."
    )


@router.put("/stories/{story_id}", dependencies=[Depends(get_admin_user)])
async def update_story(story_id: str, update: StoryUpdateRequest):
    """Update story metadata"""
    try:
        updated = False
        
        # Try to update in MySQL (saved stories)
        if update.title:
            try:
                with get_db_cursor(commit=True) as cursor:
                    cursor.execute("UPDATE user_stories SET name = %s WHERE story_id = %s", (update.title, story_id))
                    if cursor.rowcount > 0:
                        updated = True
            except Exception as e:
                logger.warning(f"Could not update saved story: {e}")
        
        # Try to update in SQLite (generated stories)
        if update.title:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("UPDATE stories SET title = ? WHERE story_id = ?", (update.title, story_id))
                if cursor.rowcount > 0:
                    updated = True
                conn.commit()
                conn.close()
            except Exception as e:
                logger.warning(f"Could not update generated story: {e}")
        
        if not updated:
            raise HTTPException(status_code=404, detail="Story not found")
        
        return {"success": True, "message": "Story updated"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update story failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
# --- Credit grants (support/comp cases) ---

@router.post("/users/{user_id}/grant-credits", dependencies=[Depends(get_admin_user)])
async def admin_grant_credits(user_id: int, payload: GrantCreditsRequest):
    """Admin-only credit adjustment. Logged in credit_transactions ('admin_grant') like every other credit change."""
    if payload.amount == 0:
        raise HTTPException(status_code=400, detail="amount must be non-zero")
    with get_db_cursor(commit=True) as cursor:
        cursor.execute("UPDATE users SET credits_balance = credits_balance + %s WHERE id = %s", (payload.amount, user_id))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="User not found")
        cursor.execute(
            "INSERT INTO credit_transactions (user_id, delta, reason) VALUES (%s, %s, 'admin_grant')",
            (user_id, payload.amount)
        )
    return {"success": True, "message": f"{payload.amount:+d} credits applied"}


# --- Subscription plans (hot-editable pricing - no deploy needed to change these) ---

@router.get("/plans", dependencies=[Depends(get_admin_user)])
async def admin_list_plans():
    """All plans including inactive ones, for the Manage Plans admin tab."""
    with get_db_cursor() as cursor:
        cursor.execute("SELECT * FROM subscription_plans ORDER BY sort_order")
        return {"success": True, "plans": cursor.fetchall()}

@router.put("/plans/{tier_key}", dependencies=[Depends(get_admin_user)])
async def admin_update_plan(tier_key: str, update: PlanUpdateRequest):
    """Edit a plan's price/credits/active state/copy. Takes effect on the very next request - no deploy."""
    updates = []
    values = []
    for field in ("display_name", "price_display", "stripe_price_id", "credits_included", "is_active",
                  "sort_order", "description", "features", "is_recommended"):
        value = getattr(update, field)
        if value is not None:
            updates.append(f"{field} = %s")
            values.append(value)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    with get_db_cursor() as cursor:
        cursor.execute("SELECT tier_key FROM subscription_plans WHERE tier_key = %s", (tier_key,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Plan not found")

    values.append(tier_key)
    with get_db_cursor(commit=True) as cursor:
        # Only one plan can be "recommended" at a time - clear the others first.
        if update.is_recommended is True:
            cursor.execute("UPDATE subscription_plans SET is_recommended = FALSE WHERE tier_key != %s", (tier_key,))
        cursor.execute(f"UPDATE subscription_plans SET {', '.join(updates)} WHERE tier_key = %s", values)

    return {"success": True, "message": "Plan updated"}


# --- Promo codes / discount vouchers (also hot-editable, no deploy needed) ---

@router.get("/promo-codes", dependencies=[Depends(get_admin_user)])
async def admin_list_promo_codes():
    with get_db_cursor() as cursor:
        cursor.execute("SELECT * FROM promo_codes ORDER BY created_at DESC")
        return {"success": True, "promo_codes": cursor.fetchall()}

@router.post("/promo-codes", dependencies=[Depends(get_admin_user)])
async def admin_create_promo_code(payload: PromoCodeCreateRequest):
    code = payload.code.strip().upper()
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(
                """INSERT INTO promo_codes
                   (code, discount_type, discount_value, stripe_coupon_id, applies_to_tier,
                    max_redemptions, max_redemptions_per_user, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (code, payload.discount_type, payload.discount_value, payload.stripe_coupon_id,
                 payload.applies_to_tier, payload.max_redemptions, payload.max_redemptions_per_user,
                 payload.expires_at)
            )
    except mysql.connector.IntegrityError:
        raise HTTPException(status_code=409, detail=f"Promo code '{code}' already exists")
    return {"success": True, "code": code}

@router.put("/promo-codes/{code}", dependencies=[Depends(get_admin_user)])
async def admin_update_promo_code(code: str, update: PromoCodeUpdateRequest):
    code = code.strip().upper()
    updates = []
    values = []
    for field in ("discount_value", "stripe_coupon_id", "max_redemptions", "max_redemptions_per_user", "expires_at", "is_active"):
        value = getattr(update, field)
        if value is not None:
            updates.append(f"{field} = %s")
            values.append(value)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    with get_db_cursor() as cursor:
        cursor.execute("SELECT code FROM promo_codes WHERE code = %s", (code,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Promo code not found")

    values.append(code)
    with get_db_cursor(commit=True) as cursor:
        cursor.execute(f"UPDATE promo_codes SET {', '.join(updates)} WHERE code = %s", values)

    return {"success": True, "message": "Promo code updated"}

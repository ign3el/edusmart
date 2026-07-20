"""
Story Storage Manager
Handles generated_stories folder structure with time-based cleanup
"""
import os
import json
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
import logging
import asyncio

logger = logging.getLogger(__name__)

GENERATED_STORIES_DIR = "generated_stories"
SAVED_STORIES_DIR = "saved_stories"
STORY_TTL_HOURS = 24

class StoryStorageManager:
    """Manages story folders in generated_stories with time-based cleanup"""
    
    def __init__(self):
        """Initialize storage directories"""
        os.makedirs(GENERATED_STORIES_DIR, exist_ok=True)
        os.makedirs(SAVED_STORIES_DIR, exist_ok=True)
        logger.info(f"Story storage initialized: {GENERATED_STORIES_DIR}, {SAVED_STORIES_DIR}")
    
    def create_story_folder(self, story_id: str, metadata: dict | None = None) -> str:
        """
        Create a new story folder in generated_stories with metadata
        
        Args:
            story_id: Unique story identifier
            metadata: Optional metadata dict (title, user_id, etc.)
            
        Returns:
            Path to the created story folder
        """
        story_dir = os.path.join(GENERATED_STORIES_DIR, story_id)
        os.makedirs(story_dir, exist_ok=True)
        
        # Create metadata file with creation timestamp
        meta = {
            "story_id": story_id,
            "created_at": time.time(),
            "created_timestamp": datetime.now().isoformat(),
            **(metadata or {})
        }
        
        meta_path = os.path.join(story_dir, "metadata.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        
        logger.info(f"✅ Created story folder: {story_dir}")
        return story_dir
    
    def get_story_path(self, story_id: str, in_saved: bool = False) -> str:
        """Get the full path to a story folder"""
        # Prevent path traversal attacks
        if ".." in story_id or "/" in story_id or "\\" in story_id:
            raise ValueError(f"Invalid story_id: contains path traversal characters")
        base_dir = SAVED_STORIES_DIR if in_saved else GENERATED_STORIES_DIR
        return os.path.join(base_dir, story_id)
    
    def story_exists(self, story_id: str, in_saved: bool = False) -> bool:
        """Check if a story folder exists"""
        return os.path.exists(self.get_story_path(story_id, in_saved))
    
    def get_metadata(self, story_id: str, in_saved: bool = False) -> dict:
        """Load metadata from a story folder"""
        meta_path = os.path.join(self.get_story_path(story_id, in_saved), "metadata.json")
        
        if not os.path.exists(meta_path):
            logger.warning(f"Metadata file not found: {meta_path}")
            return {}
        
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load metadata: {e}")
            return {}
    
    def update_metadata(self, story_id: str, updates: dict, in_saved: bool = False):
        """Update metadata for a story"""
        meta_path = os.path.join(self.get_story_path(story_id, in_saved), "metadata.json")
        
        # Load existing metadata
        metadata = self.get_metadata(story_id, in_saved)
        
        # Update with new data
        metadata.update(updates)
        metadata["updated_at"] = time.time()
        metadata["updated_timestamp"] = datetime.now().isoformat()
        
        # Save updated metadata
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
    
    def save_file(self, story_id: str, filename: str, content: bytes, in_saved: bool = False) -> str:
        """
        Save a file to a story folder with version tracking
        
        Returns:
            URL path to access the file
        """
        story_dir = self.get_story_path(story_id, in_saved)
        
        if not os.path.exists(story_dir):
            logger.error(f"Story folder does not exist: {story_dir}")
            raise FileNotFoundError(f"Story {story_id} not found")
        
        file_path = os.path.join(story_dir, filename)
        
        # Check if file already exists and handle versioning
        if os.path.exists(file_path):
            # Get current timestamp for versioning
            timestamp = time.time()
            
            # Create backup of existing file with timestamp
            backup_name = f"{filename}.backup.{int(timestamp)}"
            backup_path = os.path.join(story_dir, backup_name)
            shutil.copy2(file_path, backup_path)
            logger.info(f"📦 Backed up existing file: {filename} -> {backup_name}")
            
            # Update metadata with version info
            metadata = self.get_metadata(story_id, in_saved)
            if "file_versions" not in metadata:
                metadata["file_versions"] = {}
            
            # Track version history
            if filename not in metadata["file_versions"]:
                metadata["file_versions"][filename] = []
            
            metadata["file_versions"][filename].append({
                "version": len(metadata["file_versions"][filename]) + 1,
                "filename": backup_name,
                "created_at": timestamp,
                "created_timestamp": datetime.now().isoformat()
            })
            
            self.update_metadata(story_id, metadata, in_saved)
        
        # Write new file
        with open(file_path, "wb") as f:
            f.write(content)
        
        # Update file modification timestamp in metadata
        metadata = self.get_metadata(story_id, in_saved)
        if "file_timestamps" not in metadata:
            metadata["file_timestamps"] = {}
        metadata["file_timestamps"][filename] = time.time()
        self.update_metadata(story_id, metadata, in_saved)
        
        # Return API URL path
        api_prefix = "/api/saved-stories" if in_saved else "/api/generated-stories"
        return f"{api_prefix}/{story_id}/{filename}"
    
    def move_to_saved(self, story_id: str, saved_story_id: str | None = None) -> str:
        """
        Move a story from generated_stories to saved_stories
        
        Args:
            story_id: Current story ID in generated_stories
            saved_story_id: New ID in saved_stories (defaults to same ID)
            
        Returns:
            Path to the saved story folder
        """
        if saved_story_id is None:
            saved_story_id = story_id
        
        src_path = self.get_story_path(story_id, in_saved=False)
        dest_path = self.get_story_path(saved_story_id, in_saved=True)
        
        if not os.path.exists(src_path):
            logger.error(f"Source story not found: {src_path}")
            raise FileNotFoundError(f"Story {story_id} not found in generated_stories")
        
        if os.path.exists(dest_path):
            logger.warning(f"Destination already exists, removing: {dest_path}")
            shutil.rmtree(dest_path)
        
        # Move the entire folder
        shutil.move(src_path, dest_path)
        
        # Update metadata
        self.update_metadata(saved_story_id, {
            "saved_at": time.time(),
            "saved_timestamp": datetime.now().isoformat(),
            "original_story_id": story_id
        }, in_saved=True)
        
        logger.info(f"✅ Moved story {story_id} -> saved_stories/{saved_story_id}")
        return dest_path
    
    def delete_story(self, story_id: str, in_saved: bool = False):
        """Delete a story folder and all its contents.

        For generated (unsaved) stories, also purges the matching job_state.db
        rows - this used to only delete the folder, leaving a permanent
        orphaned record behind in job_state.db every time a story expired.
        """
        story_path = self.get_story_path(story_id, in_saved)

        if not os.path.exists(story_path):
            logger.warning(f"Story folder already deleted: {story_path}")
        else:
            try:
                shutil.rmtree(story_path)
                logger.info(f"🗑️ Deleted story folder: {story_path}")
            except Exception as e:
                logger.error(f"Failed to delete story {story_id}: {e}")

        if not in_saved:
            try:
                from job_state import job_manager
                job_manager.delete_story(story_id)
            except Exception as e:
                logger.error(f"Failed to delete job_state.db rows for {story_id}: {e}")

    def cleanup_expired_stories(self) -> int:
        """
        Clean up stories older than TTL from generated_stories only.
        Saved stories are never auto-deleted by this scheduler.
        
        Returns:
            Number of stories deleted
        """
        now = time.time()
        cutoff_time = now - (STORY_TTL_HOURS * 3600)
        deleted_count = 0
        
        logger.info(f"🧹 Running cleanup for generated stories older than {STORY_TTL_HOURS}h...")
        
        if not os.path.exists(GENERATED_STORIES_DIR):
            return 0
        
        for story_id in os.listdir(GENERATED_STORIES_DIR):
            story_path = os.path.join(GENERATED_STORIES_DIR, story_id)
            
            if not os.path.isdir(story_path):
                continue
            
            # Load metadata to get creation time
            metadata = self.get_metadata(story_id, in_saved=False)
            created_at = metadata.get("created_at", 0)
            
            if created_at == 0:
                # Fallback to folder creation time
                created_at = os.path.getctime(story_path)
                logger.warning(f"No created_at in metadata for {story_id}, using folder ctime")
            
            if created_at < cutoff_time:
                age_hours = (now - created_at) / 3600
                logger.info(f"🗑️ Deleting expired generated story {story_id} (age: {age_hours:.1f}h)")
                self.delete_story(story_id, in_saved=False)
                deleted_count += 1
        
        if deleted_count > 0:
            logger.info(f"✅ Cleanup complete: {deleted_count} generated stories deleted")
        else:
            logger.info("✅ Cleanup complete: No expired generated stories found")
        
        return deleted_count
    
    def list_generated_stories(self) -> list:
        """List all stories in generated_stories with metadata"""
        stories = []
        
        if not os.path.exists(GENERATED_STORIES_DIR):
            return stories
        
        for story_id in os.listdir(GENERATED_STORIES_DIR):
            story_path = os.path.join(GENERATED_STORIES_DIR, story_id)
            
            if not os.path.isdir(story_path):
                continue
            
            metadata = self.get_metadata(story_id, in_saved=False)
            stories.append({
                "story_id": story_id,
                "path": story_path,
                **metadata
            })
        
        return stories
    
    def get_story_age_hours(self, story_id: str, in_saved: bool = False) -> float:
        """Get the age of a story in hours"""
        metadata = self.get_metadata(story_id, in_saved)
        created_at = metadata.get("created_at", 0)
        
        if created_at == 0:
            # Fallback to folder creation time
            story_path = self.get_story_path(story_id, in_saved)
            if os.path.exists(story_path):
                created_at = os.path.getctime(story_path)
        
        return (time.time() - created_at) / 3600
    
    def get_latest_files(self, story_id: str, in_saved: bool = False) -> dict:
        """
        Get the most recent version of each file in a story folder.
        Handles duplicate files by selecting the most recently modified one.
        Supports both old format (scene_0.png) and new format (uuid_scene_0.png).
        
        Returns:
            Dictionary mapping scene numbers to latest file paths
        """
        story_path = self.get_story_path(story_id, in_saved)
        if not os.path.exists(story_path):
            return {}
        
        # Get all files in the story directory
        all_files = []
        for filename in os.listdir(story_path):
            file_path = os.path.join(story_path, filename)
            if os.path.isfile(file_path):
                all_files.append((filename, file_path))
        
        # Group files by scene number and type
        scene_files = {}
        for filename, file_path in all_files:
            # Skip metadata files and backups
            if filename in ["metadata.json"] or ".backup." in filename:
                continue
            
            # Try to extract scene number and type from filename
            # Support both formats:
            # 1. New format: {uuid}_scene_{number}.{ext}
            # 2. Old format: scene_{number}.{ext}
            
            scene_num = None
            file_type = None
            
            # Check for new format first
            if "_scene_" in filename:
                parts = filename.split("_")
                # Find the scene part
                for i, part in enumerate(parts):
                    if part == "scene" and i + 1 < len(parts):
                        try:
                            scene_num = int(parts[i + 1].split(".")[0])
                            break
                        except ValueError:
                            continue
            
            # Check for old format if new format didn't work
            if scene_num is None and filename.startswith("scene_"):
                try:
                    # Extract number from "scene_{number}.{ext}"
                    base_name = filename.split(".")[0]  # Remove extension
                    scene_num_str = base_name.replace("scene_", "")
                    scene_num = int(scene_num_str)
                except ValueError:
                    continue
            
            # Skip if we couldn't extract a scene number
            if scene_num is None:
                continue
            
            # Determine file type
            file_ext = filename.split(".")[-1].lower()
            if file_ext in ["png", "jpg", "jpeg"]:
                file_type = "image"
            elif file_ext in ["mp3", "wav", "ogg"]:
                file_type = "audio"
            else:
                continue
            
            # Create key for this scene
            key = f"{scene_num}_{file_type}"
            
            if key not in scene_files:
                scene_files[key] = []
            
            # Get file modification time
            mtime = os.path.getmtime(file_path)
            
            scene_files[key].append({
                "path": file_path,
                "filename": filename,
                "mtime": mtime
            })
        
        # Select the most recent file for each scene/type
        latest_files = {}
        for key, files in scene_files.items():
            if files:
                # Sort by modification time (most recent first)
                files.sort(key=lambda x: x["mtime"], reverse=True)
                latest_files[key] = files[0]
        
        return latest_files
    
    def reconstruct_story_from_files(self, story_id: str, in_saved: bool = False) -> dict:
        """
        Reconstruct story.json from files, handling duplicates by using most recent versions.
        
        Returns:
            Reconstructed story data or empty dict if reconstruction fails
        """
        story_path = self.get_story_path(story_id, in_saved)
        if not os.path.exists(story_path):
            return {}
        
        # Get latest files for each scene
        latest_files = self.get_latest_files(story_id, in_saved)
        
        if not latest_files:
            return {}
        
        # Group by scene number
        scenes = {}
        for key, file_info in latest_files.items():
            scene_num, file_type = key.split("_")
            scene_num = int(scene_num)
            
            if scene_num not in scenes:
                scenes[scene_num] = {"image": None, "audio": None}
            
            scenes[scene_num][file_type] = file_info
        
        # Build scene list
        scene_list = []
        for scene_num in sorted(scenes.keys()):
            scene_data = scenes[scene_num]
            if scene_data["image"] and scene_data["audio"]:
                scene_list.append({
                    "scene_number": scene_num,
                    "image_path": scene_data["image"]["filename"],
                    "audio_path": scene_data["audio"]["filename"],
                    "file_timestamps": {
                        "image": scene_data["image"]["mtime"],
                        "audio": scene_data["audio"]["mtime"]
                    }
                })
        
        # Try to load metadata
        metadata = self.get_metadata(story_id, in_saved)
        
        # Return reconstructed data
        return {
            "story_id": story_id,
            "scenes": scene_list,
            "metadata": metadata,
            "reconstructed": True,
            "duplicate_handling": "latest_version"
        }


# Singleton instance
storage_manager = StoryStorageManager()


async def cleanup_scheduler_task():
    """Background task that runs cleanup every hour.

    Runs once shortly after startup, then every hour after that - a
    sleep-then-run loop means a container that gets redeployed more often
    than the interval (common during active development) could go its
    entire life without ever completing a single cleanup pass.
    """
    logger.info("🚀 Starting story cleanup scheduler")
    await asyncio.sleep(60)  # let startup finish before the first pass

    while True:
        try:
            storage_manager.cleanup_expired_stories()
        except Exception as e:
            logger.error(f"❌ Error in cleanup scheduler: {e}", exc_info=True)
            # Continue running even if cleanup fails
        await asyncio.sleep(3600)  # Run every hour


async def database_cleanup_scheduler_task():
    """
    Background task that runs database cleanup every 2 days (48 hours).
    Removes orphaned stories and old temporary data from both MySQL and SQLite.

    Runs once shortly after startup, then every 48h after that - a
    sleep-then-run loop means a container redeployed more often than the
    interval could go its entire life without ever completing a pass. That
    was exactly why job_state.db still had 50 orphaned rows despite this
    scheduler supposedly cleaning them up every 2 days.
    """
    logger.info("🚀 Starting database cleanup scheduler (runs every 2 days)")
    await asyncio.sleep(120)  # let startup finish before the first pass

    while True:
        try:
            logger.info("🧹 Running database cleanup...")
            
            # Import here to avoid circular imports
            from database import get_db_cursor
            import time
            
            # ====== MySQL Cleanup ======
            
            # Calculate cutoff time (2 days ago)
            cutoff_timestamp = datetime.now() - timedelta(days=2)
            deleted_count = 0
            
            with get_db_cursor(commit=True) as cursor:
                # Find stories in database that don't exist in saved_stories folder
                cursor.execute("""
                    SELECT story_id, name, created_at 
                    FROM user_stories 
                    WHERE created_at < %s
                """, (cutoff_timestamp,))
                
                old_stories = cursor.fetchall()
                
                for story in old_stories:
                    story_id = story['story_id']
                    story_name = story.get('name', 'Unknown')
                    
                    # Check if story folder exists in saved_stories
                    saved_path = storage_manager.get_story_path(story_id, in_saved=True)
                    
                    if not os.path.exists(saved_path):
                        # Story was never properly saved or files were deleted
                        logger.warning(f"🗑️ Orphaned database record found: {story_name} ({story_id})")
                        
                        # Delete from database
                        cursor.execute("DELETE FROM user_stories WHERE story_id = %s", (story_id,))
                        deleted_count += 1
                        logger.info(f"✅ Deleted orphaned story record: {story_id}")
            
            # Clean up old email verifications (older than 7 days)
            email_cutoff = datetime.now() - timedelta(days=7)
            with get_db_cursor(commit=True) as cursor:
                cursor.execute("DELETE FROM email_verifications WHERE created_at < %s", (email_cutoff,))
                email_deleted = cursor.rowcount
                if email_deleted > 0:
                    logger.info(f"✅ Deleted {email_deleted} old email verification records")
            
            # Clean up old password reset tokens (older than 1 day)
            token_cutoff = datetime.now() - timedelta(days=1)
            with get_db_cursor(commit=True) as cursor:
                cursor.execute("DELETE FROM password_reset_tokens WHERE expires_at < %s", (token_cutoff,))
                token_deleted = cursor.rowcount
                if token_deleted > 0:
                    logger.info(f"✅ Deleted {token_deleted} expired password reset tokens")
            
            logger.info(f"✅ MySQL cleanup complete: {deleted_count} orphaned stories, {email_deleted} old verifications, {token_deleted} expired tokens")
            
            # ====== SQLite (job_state.db) Cleanup ======
            
            import sqlite3
            
            try:
                # Connect to job state database (must match job_state.py's
                # actual path - this used to point at a stale file in the
                # source tree instead of the live database)
                conn = sqlite3.connect("db_data/job_state.db")
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # Delete old completed/failed jobs (older than 2 days)
                cutoff_sqlite = datetime.now() - timedelta(days=2)
                
                # Get count before deletion for logging
                cursor.execute("""
                    SELECT COUNT(*) as count FROM stories 
                    WHERE created_at < ? AND status IN ('completed', 'failed')
                """, (cutoff_sqlite,))
                old_jobs_count = cursor.fetchone()[0]
                
                if old_jobs_count > 0:
                    # Delete old stories from stories table
                    cursor.execute("""
                        DELETE FROM stories 
                        WHERE created_at < ? AND status IN ('completed', 'failed')
                    """, (cutoff_sqlite,))
                    
                    # Delete orphaned scenes (scenes without parent story)
                    cursor.execute("""
                        DELETE FROM scenes 
                        WHERE story_id NOT IN (SELECT story_id FROM stories)
                    """)
                    orphaned_scenes = cursor.rowcount
                    
                    conn.commit()
                    logger.info(f"✅ SQLite cleanup complete: {old_jobs_count} old jobs, {orphaned_scenes} orphaned scenes")
                else:
                    logger.info("✅ SQLite cleanup complete: No old jobs to clean")
                
                # Vacuum database to reclaim space
                cursor.execute("VACUUM")
                conn.commit()
                logger.info("✅ SQLite database vacuumed")
                
                conn.close()
                
            except Exception as sqlite_error:
                logger.error(f"❌ SQLite cleanup error: {sqlite_error}", exc_info=True)
            
            logger.info("✅ Complete database cleanup finished")

        except Exception as e:
            logger.error(f"❌ Error in database cleanup scheduler: {e}", exc_info=True)
            # Continue running even if cleanup fails
        await asyncio.sleep(48 * 3600)  # Run every 2 days

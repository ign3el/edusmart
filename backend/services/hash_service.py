"""
Hash Service for File Verification
Handles SHA-256 hash generation and duplicate detection across saved and generated stories
"""
import os
import hashlib
import json
import time
from typing import Optional, Dict, List
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class HashService:
    """Service for file hash generation and duplicate detection"""
    
    def __init__(self):
        self.saved_stories_dir = Path("saved_stories")
        self.generated_stories_dir = Path("generated_stories")
        # db_data is the Docker named volume that already holds job_state.db and
        # the RunPod usage counter: writable by the container user, and persistent
        # across rebuilds. The old location (backend/hash_cache.json -> a path
        # inside the source tree) only worked by accident of the bind mount.
        self.hash_cache_file = Path("db_data/hash_cache.json")
        self._legacy_cache_file = Path("backend/hash_cache.json")
        self.hash_cache = self._load_hash_cache()
    
    def _load_hash_cache(self) -> Dict[str, Dict]:
        """Load hash cache from disk"""
        # Carry the old file over on first run so the migration doesn't throw
        # away a warm cache and re-flag every recent upload as new.
        for path in (self.hash_cache_file, getattr(self, "_legacy_cache_file", None)):
            if path and path.exists():
                try:
                    with open(path, 'r') as f:
                        return json.load(f)
                except Exception as e:
                    logger.warning(f"Failed to load hash cache from {path}: {e}")
        return {}
    
    def _save_hash_cache(self):
        """Save hash cache to disk"""
        try:
            # Drop expired entries before writing - now that negative results are
            # cached too (every upload, not just actual duplicates), this file
            # would otherwise grow by one entry per unique file ever checked.
            now = time.time()
            self.hash_cache = {
                k: v for k, v in self.hash_cache.items()
                if now - v.get("timestamp", 0) < (86400 if v.get("duplicate_info") else 300)
            }
            self.hash_cache_file.parent.mkdir(parents=True, exist_ok=True)
            # Convert Path objects to strings before JSON serialization
            serializable_cache = {}
            for key, value in self.hash_cache.items():
                if isinstance(value, dict):
                    serializable_value = {}
                    for k, v in value.items():
                        if isinstance(v, Path):
                            serializable_value[k] = str(v.resolve())
                        elif isinstance(v, dict):
                            # Handle nested dicts
                            nested = {}
                            for nk, nv in v.items():
                                if isinstance(nv, Path):
                                    nested[nk] = str(nv.resolve())
                                else:
                                    nested[nk] = nv
                            serializable_value[k] = nested
                        else:
                            serializable_value[k] = v
                    serializable_cache[key] = serializable_value
                else:
                    serializable_cache[key] = value
            
            with open(self.hash_cache_file, 'w') as f:
                json.dump(serializable_cache, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save hash cache: {e}")
    
    def generate_file_hash(self, file_path: str) -> str:
        """
        Generate SHA-256 hash for a file
        
        Args:
            file_path: Path to the file
            
        Returns:
            SHA-256 hash as hex string
        """
        try:
            with open(file_path, 'rb') as f:
                file_hash = hashlib.sha256()
                # Read in chunks to handle large files
                while chunk := f.read(8192):
                    file_hash.update(chunk)
                return file_hash.hexdigest()
        except Exception as e:
            logger.error(f"Error generating hash for {file_path}: {e}")
            raise
    
    def generate_bytes_hash(self, file_bytes: bytes) -> str:
        """
        Generate SHA-256 hash for file bytes
        
        Args:
            file_bytes: File content as bytes
            
        Returns:
            SHA-256 hash as hex string
        """
        return hashlib.sha256(file_bytes).hexdigest()
    
    def scan_directory_for_hash(self, target_hash: str, directory: Path) -> List[Dict]:
        """
        Scan a directory for files matching the target hash
        
        Args:
            target_hash: Hash to search for
            directory: Directory to scan
            
        Returns:
            List of matching file info dicts
        """
        matches = []
        
        if not directory.exists():
            return matches
        
        for story_dir in directory.iterdir():
            if not story_dir.is_dir():
                continue
            
            # Check metadata first if available
            metadata_file = story_dir / "metadata.json"
            if metadata_file.exists():
                try:
                    with open(metadata_file, 'r') as f:
                        metadata = json.load(f)
                        cached_hash = metadata.get("file_hash")
                        if cached_hash == target_hash:
                            matches.append({
                                "story_id": story_dir.name,
                                "path": str(story_dir),
                                "source": "metadata",
                                "metadata": metadata
                            })
                        if cached_hash:
                            # Metadata has an authoritative hash for this story and
                            # we already compared it above - falling through to
                            # re-hash every generated file in the directory just to
                            # re-confirm a non-match turned every upload into an
                            # O(every file in every past story) SHA-256 scan, done
                            # synchronously before the job is even created.
                            continue
                except Exception:
                    pass

            # Fallback for legacy stories with no cached hash in metadata
            for file_path in story_dir.rglob("*"):
                if file_path.is_file() and not file_path.name.endswith('.json'):
                    try:
                        file_hash = self.generate_file_hash(str(file_path))
                        if file_hash == target_hash:
                            matches.append({
                                "story_id": story_dir.name,
                                "path": str(file_path),
                                "source": "file_scan",
                                "relative_path": str(file_path.relative_to(story_dir))
                            })
                    except Exception as e:
                        logger.warning(f"Could not hash {file_path}: {e}")
        
        return matches
    
    def find_duplicate(self, file_bytes: bytes, file_name: Optional[str] = None) -> Optional[Dict]:
        """
        Find if file content already exists in saved or generated stories
        
        Args:
            file_bytes: File content as bytes
            file_name: Optional filename for context
            
        Returns:
            Dict with duplicate info including story metadata
        """
        file_hash = self.generate_bytes_hash(file_bytes)

        # Check cache first (for performance). "No duplicate" results are cached
        # too (short TTL) - not just matches - because the frontend always calls
        # /api/check-duplicate immediately before /api/upload calls this same
        # function again for the same file, seconds apart. Without caching the
        # (far more common) negative result, every single upload paid for the
        # full directory scan twice in a row.
        cache_key = f"{file_hash}"
        if cache_key in self.hash_cache:
            cached = self.hash_cache[cache_key]
            ttl = 86400 if cached.get("duplicate_info") else 300
            if time.time() - cached.get("timestamp", 0) < ttl:
                logger.info(f"Hash found in cache: {file_hash[:16]}...")
                return cached.get("duplicate_info")

        # Scan both directories
        saved_matches = self.scan_directory_for_hash(file_hash, self.saved_stories_dir)
        generated_matches = self.scan_directory_for_hash(file_hash, self.generated_stories_dir)
        
        all_matches = saved_matches + generated_matches
        
        if all_matches:
            # Get the first match (most recent) and read its metadata
            first_match = all_matches[0]
            story_id = first_match.get("story_id", "")
            
            # Try to read metadata for this story
            created_by = "Unknown"
            created_at = "Unknown"
            story_title = "Unknown"
            
            for match in all_matches:
                meta = None
                # If match came from metadata scan, use it directly
                if match.get("source") == "metadata" and match.get("metadata"):
                    meta = match["metadata"]
                else:
                    # For file_scan matches, read metadata.json from parent dir
                    metadata_path = os.path.join(os.path.dirname(match.get("path", "")), "metadata.json")
                    if os.path.exists(metadata_path):
                        try:
                            with open(metadata_path, 'r') as f:
                                meta = json.load(f)
                        except Exception as e:
                            logger.warning(f"Could not read metadata for {story_id}: {e}")
                
                if meta:
                    created_by = meta.get("user_email", meta.get("username", meta.get("created_by", "Unknown")))
                    created_at = meta.get("created_at", meta.get("timestamp", "Unknown"))
                    story_title = meta.get("title", meta.get("story_title", "Unknown"))
                    break
            
            duplicate_info = {
                "hash": file_hash,
                "story_id": story_id,
                "created_by": created_by,
                "created_at": created_at,
                "story_title": story_title,
                "is_duplicate": True,
                "matches": all_matches,
                "saved_stories": saved_matches,
                "generated_stories": generated_matches
            }
            
            self.hash_cache[cache_key] = {
                "timestamp": time.time(),
                "duplicate_info": duplicate_info
            }
            self._save_hash_cache()

            return duplicate_info

        # Cache the negative result too (short TTL - see comment above)
        self.hash_cache[cache_key] = {
            "timestamp": time.time(),
            "duplicate_info": None
        }
        self._save_hash_cache()

        return None
    
    def update_story_metadata_hash(self, story_id: str, file_hash: str, in_saved: bool = False):
        """
        Update story metadata with file hash
        
        Args:
            story_id: Story identifier
            file_hash: File hash to store
            in_saved: Whether story is in saved_stories
        """
        base_dir = self.saved_stories_dir if in_saved else self.generated_stories_dir
        metadata_path = base_dir / story_id / "metadata.json"
        
        if metadata_path.exists():
            try:
                with open(metadata_path, 'r') as f:
                    metadata = json.load(f)
                
                metadata["file_hash"] = file_hash
                
                with open(metadata_path, 'w') as f:
                    json.dump(metadata, f, indent=2)
                
                logger.info(f"Updated metadata with hash for story {story_id}")
            except Exception as e:
                logger.error(f"Failed to update metadata: {e}")
        else:
            # Create metadata if it doesn't exist
            try:
                base_dir.mkdir(parents=True, exist_ok=True)
                metadata = {
                    "story_id": story_id,
                    "file_hash": file_hash,
                    "created_at": str(time.time())
                }
                with open(metadata_path, 'w') as f:
                    json.dump(metadata, f, indent=2)
                logger.info(f"Created metadata with hash for story {story_id}")
            except Exception as e:
                logger.error(f"Failed to create metadata: {e}")
    
    def get_story_hash(self, story_id: str, in_saved: bool = False) -> Optional[str]:
        """
        Get file hash for a story from metadata
        
        Args:
            story_id: Story identifier
            in_saved: Whether story is in saved_stories
            
        Returns:
            File hash if found, None otherwise
        """
        base_dir = self.saved_stories_dir if in_saved else self.generated_stories_dir
        metadata_path = base_dir / story_id / "metadata.json"
        
        if metadata_path.exists():
            try:
                with open(metadata_path, 'r') as f:
                    metadata = json.load(f)
                    return metadata.get("file_hash")
            except Exception:
                return None
        
        return None
    
    def clear_old_cache(self, max_age_hours: int = 24):
        """Remove cache entries older than specified hours"""
        import time
        current_time = time.time()
        max_age_seconds = max_age_hours * 3600
        
        to_remove = []
        for key, data in self.hash_cache.items():
            if current_time - data.get("timestamp", 0) > max_age_seconds:
                to_remove.append(key)
        
        for key in to_remove:
            del self.hash_cache[key]
        
        if to_remove:
            logger.info(f"Cleared {len(to_remove)} old cache entries")
            self._save_hash_cache()

# Singleton instance
hash_service = HashService()

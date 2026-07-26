"""
Job state manager using SQLite for scene-level tracking.
Isolated per application, no Redis required.
"""
import os
import sqlite3
import json
import threading
from typing import Any, Optional, Dict, List
from datetime import datetime
from contextlib import contextmanager

# How long a blocked SQLite writer waits for the lock before giving up.
# Env-driven: a busier host wants a longer window, not a code change.
BUSY_TIMEOUT_MS = int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "30000"))

class JobStateManager:
    def __init__(self, db_path: str = "db_data/job_state.db"):
        self.db_path = db_path
        self._local = threading.local()
        # Ensure directory exists
        import os
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
    
    @contextmanager
    def _get_conn(self):
        """Thread-safe connection management."""
        if not hasattr(self._local, 'conn'):
            conn = sqlite3.connect(
                self.db_path, check_same_thread=False, timeout=BUSY_TIMEOUT_MS / 1000
            )
            conn.row_factory = sqlite3.Row
            # The default journal_mode=delete takes an EXCLUSIVE lock over the
            # whole database for every write and blocks all readers while it is
            # held, with synchronous=FULL forcing an fsync per commit. Scene
            # status updates from concurrent stories therefore serialise against
            # each other and eventually raise "database is locked". WAL lets
            # readers proceed during a write and serialises only writer against
            # writer; busy_timeout makes a blocked writer wait its turn instead
            # of failing immediately. synchronous=NORMAL drops one fsync per
            # commit, which is safe under WAL - a host crash can lose the last
            # few scene-status updates, and the generation worker rewrites those
            # anyway. WAL is also a hard prerequisite for uvicorn --workers > 1,
            # since it is what makes multi-process access to this file safe.
            conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        try:
            yield self._local.conn
        except Exception:
            self._local.conn.rollback()
            raise
        else:
            self._local.conn.commit()
    
    def _init_db(self):
        """Initialize database schema."""
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stories (
                    story_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    title TEXT,
                    grade_level TEXT,
                    total_scenes INTEGER,
                    completed_scenes INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    file_hash TEXT,
                    user_id INTEGER,
                    username TEXT,
                    quiz TEXT
                )
            """)
            
            # Migrate existing tables - add new columns if they don't exist
            try:
                # Check if file_hash column exists
                cursor = conn.execute("PRAGMA table_info(stories)")
                columns = [row[1] for row in cursor.fetchall()]
                
                if 'file_hash' not in columns:
                    conn.execute("ALTER TABLE stories ADD COLUMN file_hash TEXT")
                if 'user_id' not in columns:
                    conn.execute("ALTER TABLE stories ADD COLUMN user_id INTEGER")
                if 'username' not in columns:
                    conn.execute("ALTER TABLE stories ADD COLUMN username TEXT")
                if 'quiz' not in columns:
                    conn.execute("ALTER TABLE stories ADD COLUMN quiz TEXT")
            except Exception as e:
                # If migration fails, log it but continue (table might be new)
                print(f"Migration info: {e}")
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scenes (
                    scene_id TEXT PRIMARY KEY,
                    story_id TEXT NOT NULL,
                    scene_index INTEGER NOT NULL,
                    text TEXT,
                    image_status TEXT DEFAULT 'pending',
                    audio_status TEXT DEFAULT 'pending',
                    image_url TEXT,
                    audio_url TEXT,
                    character_prompt TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (story_id) REFERENCES stories(story_id)
                )
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_scenes_story 
                ON scenes(story_id, scene_index)
            """)

            # Durable generation queue. Lives in this database rather than a
            # separate store because it must be updated in the same place, with
            # the same WAL settings, as the story rows it refers to - and
            # because a queue that does not survive a restart is not a queue,
            # it is a list of promises the app forgets it made.
            #
            # seq is an AUTOINCREMENT surrogate key, not created_at: timestamps
            # here have one-second resolution, so a burst of uploads within the
            # same second would have no defined order and FIFO fairness would
            # quietly become arbitrary.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS generation_queue (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    story_id TEXT NOT NULL UNIQUE,
                    user_id INTEGER,
                    payload TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    enqueued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP,
                    finished_at TIMESTAMP,
                    error TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_queue_state
                ON generation_queue(state, seq)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_queue_user
                ON generation_queue(user_id, state)
            """)
            
            # Only create index if file_hash column exists
            try:
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_stories_file_hash 
                    ON stories(file_hash, created_at)
                """)
            except Exception as e:
                print(f"Index creation skipped: {e}")
    
    def initialize_story(self, story_id: str, grade_level: str, file_hash: Optional[str] = None, user_id: Optional[int] = None, username: Optional[str] = None):
        """Create a preliminary story job record."""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO stories (story_id, status, title, grade_level, total_scenes, completed_scenes, file_hash, user_id, username)
                VALUES (?, 'initializing', 'Initializing story...', ?, 0, 0, ?, ?, ?)
            """, (story_id, grade_level, file_hash, user_id, username))

    def update_story_metadata(self, story_id: str, title: str, total_scenes: int, quiz: Optional[List[Dict]] = None):
        """Update story metadata after initial AI processing."""
        with self._get_conn() as conn:
            if quiz is not None:
                quiz_json = json.dumps(quiz)
                conn.execute("""
                    UPDATE stories
                    SET status = 'processing', title = ?, total_scenes = ?, quiz = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE story_id = ?
                """, (title, total_scenes, quiz_json, story_id))
            else:
                conn.execute("""
                    UPDATE stories
                    SET status = 'processing', title = ?, total_scenes = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE story_id = ?
                """, (title, total_scenes, story_id))
    
    def create_scene(self, story_id: str, scene_index: int, text: str, character_prompt: Optional[str] = None):
        """Create a new scene for tracking."""
        scene_id = f"{story_id}_scene_{scene_index}"
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO scenes (scene_id, story_id, scene_index, text, character_prompt)
                VALUES (?, ?, ?, ?, ?)
            """, (scene_id, story_id, scene_index, text, character_prompt))
        return scene_id
    
    def update_scene_image(self, scene_id: str, status: str, image_url: Optional[str] = None):
        """Update scene image status."""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE scenes 
                SET image_status = ?, image_url = ?, updated_at = CURRENT_TIMESTAMP
                WHERE scene_id = ?
            """, (status, image_url, scene_id))
            self._check_story_completion(scene_id)
    
    def update_scene_audio(self, scene_id: str, status: str, audio_url: Optional[str] = None):
        """Update scene audio status."""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE scenes 
                SET audio_status = ?, audio_url = ?, updated_at = CURRENT_TIMESTAMP
                WHERE scene_id = ?
            """, (status, audio_url, scene_id))
            self._check_story_completion(scene_id)
    
    def _check_story_completion(self, scene_id: str):
        """Check if all scenes are complete and update story status."""
        with self._get_conn() as conn:
            # Get story_id from scene
            row = conn.execute("SELECT story_id FROM scenes WHERE scene_id = ?", (scene_id,)).fetchone()
            if not row:
                return
            
            story_id = row['story_id']
            
            # Count completed scenes
            result = conn.execute("""
                SELECT COUNT(*) as completed
                FROM scenes
                WHERE story_id = ? 
                AND image_status = 'completed' 
                AND audio_status = 'completed'
            """, (story_id,)).fetchone()
            
            completed = result['completed']
            
            # Get total scenes
            story = conn.execute("SELECT total_scenes FROM stories WHERE story_id = ?", (story_id,)).fetchone()
            total = story['total_scenes']
            
            # Update story
            conn.execute("""
                UPDATE stories
                SET completed_scenes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE story_id = ?
            """, (completed, story_id))
            
            # Mark as completed if all done
            if completed == total:
                conn.execute("""
                    UPDATE stories
                    SET status = 'completed', updated_at = CURRENT_TIMESTAMP
                    WHERE story_id = ?
                """, (story_id,))
    
    def delete_story(self, story_id: str) -> None:
        """Delete a story's scenes and story row. Safe to call even if the
        story doesn't exist (e.g. its folder was already cleaned up)."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM scenes WHERE story_id = ?", (story_id,))
            conn.execute("DELETE FROM stories WHERE story_id = ?", (story_id,))
            # A queued job whose story row is gone would be claimed by a worker
            # and fail on a missing folder, burning a slot for nothing.
            conn.execute("DELETE FROM generation_queue WHERE story_id = ?", (story_id,))

    def get_story_status(self, story_id: str) -> Optional[Dict]:
        """Get overall story status."""
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT * FROM stories WHERE story_id = ?
            """, (story_id,)).fetchone()
            
            if not row:
                return None
            
            return dict(row)
    
    def get_scene(self, scene_id: str) -> Optional[Dict]:
        """Get specific scene data."""
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT * FROM scenes WHERE scene_id = ?
            """, (scene_id,)).fetchone()
            
            if not row:
                return None
            
            return dict(row)
    
    def get_all_scenes(self, story_id: str) -> List[Dict]:
        """Get all scenes for a story."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM scenes 
                WHERE story_id = ?
                ORDER BY scene_index
            """, (story_id,)).fetchall()
            
            return [dict(row) for row in rows]
    
    def mark_story_failed(self, story_id: str, error: Optional[str] = None):
        """Mark story as failed."""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE stories
                SET status = 'failed', updated_at = CURRENT_TIMESTAMP
                WHERE story_id = ?
            """, (story_id,))

    def reconcile_orphaned_jobs(self) -> List[Dict]:
        """Fail any story left 'processing' from before this process started.

        Generation runs as an in-memory background task, so a story stuck at
        'processing' when the app boots belonged to a task that died with the
        previous process - it will never complete or update again. Returns the
        affected (story_id, user_id) rows so the caller can refund credits.
        """
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT story_id, user_id FROM stories WHERE status = 'processing'
            """).fetchall()
            orphaned = [dict(row) for row in rows]
            if orphaned:
                conn.execute("""
                    UPDATE stories
                    SET status = 'failed', updated_at = CURRENT_TIMESTAMP
                    WHERE status = 'processing'
                """)
            return orphaned
    
    def check_duplicate_file(self, file_hash: str, hours: int = 24) -> Optional[Dict]:
        """Check if a file with the same hash was uploaded within the specified hours."""
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT story_id, title, username, user_id, created_at, status
                FROM stories
                WHERE file_hash = ? 
                  AND created_at >= datetime('now', '-' || ? || ' hours')
                  AND status = 'completed'
                ORDER BY created_at DESC
                LIMIT 1
            """, (file_hash, hours)).fetchone()
            
            if row:
                return dict(row)
            return None

    # ==================================================================
    # Generation queue
    # ==================================================================
    # Story generation is not run inline any more (see services/job_queue.py).
    # These methods are the only place the queue table is touched, so the
    # ordering and locking rules live in one file.

    def enqueue_job(self, story_id: str, user_id: Optional[int], payload: Dict[str, Any]) -> int:
        """Record a job as queued and return its 1-based position in the queue.

        Position is counted over queued rows only, so it answers the question the
        user actually asks - "how many are ahead of me" - rather than how many
        rows the table happens to hold.
        """
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO generation_queue (story_id, user_id, payload, state)
                VALUES (?, ?, ?, 'queued')
            """, (story_id, user_id, json.dumps(payload)))
            row = conn.execute("""
                SELECT COUNT(*) AS position
                FROM generation_queue
                WHERE state = 'queued'
                  AND seq <= (SELECT seq FROM generation_queue WHERE story_id = ?)
            """, (story_id,)).fetchone()
            return row["position"] if row else 1

    def claim_next_job(self) -> Optional[Dict[str, Any]]:
        """Atomically take the oldest queued job and mark it running.

        BEGIN IMMEDIATE takes the write lock before the SELECT. A plain
        (deferred) transaction would not: SQLite only escalates to a write lock
        at the UPDATE, by which point two workers - in this process or in a
        second uvicorn worker process - have both already read the same row as
        queued, and both would run the same story. The immediate lock is what
        makes this safe to run multi-process, which is the next scaling step.
        """
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""
                SELECT * FROM generation_queue
                WHERE state = 'queued'
                ORDER BY seq
                LIMIT 1
            """).fetchone()
            if not row:
                return None
            conn.execute("""
                UPDATE generation_queue
                SET state = 'running',
                    attempts = attempts + 1,
                    started_at = CURRENT_TIMESTAMP
                WHERE seq = ?
            """, (row["seq"],))
            job = dict(row)

        job["attempts"] = job["attempts"] + 1
        job["payload"] = json.loads(job["payload"])
        return job

    def finish_job(self, story_id: str, error: Optional[str] = None) -> None:
        """Settle a running job. `error` set means it failed."""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE generation_queue
                SET state = ?, error = ?, finished_at = CURRENT_TIMESTAMP
                WHERE story_id = ?
            """, ("failed" if error else "done", error, story_id))

    def remove_queued_job(self, story_id: str) -> None:
        """Withdraw a job that has not started. A running job is left alone -
        deleting its row would not stop the worker, only hide it."""
        with self._get_conn() as conn:
            conn.execute("""
                DELETE FROM generation_queue
                WHERE story_id = ? AND state = 'queued'
            """, (story_id,))

    def queue_position(self, story_id: str) -> int:
        """How many jobs are at or ahead of this one. 0 once it has started."""
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT COUNT(*) AS position
                FROM generation_queue
                WHERE state = 'queued'
                  AND seq <= (SELECT seq FROM generation_queue
                              WHERE story_id = ? AND state = 'queued')
            """, (story_id,)).fetchone()
            return row["position"] if row else 0

    def queue_stats(self) -> Dict[str, int]:
        """Row counts by state, for admission control and /api/health."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT state, COUNT(*) AS c FROM generation_queue GROUP BY state
            """).fetchall()
            return {row["state"]: row["c"] for row in rows}

    def active_jobs_for_user(self, user_id: int) -> int:
        """Jobs this user has queued or running - the per-user admission cap."""
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT COUNT(*) AS c FROM generation_queue
                WHERE user_id = ? AND state IN ('queued', 'running')
            """, (user_id,)).fetchone()
            return row["c"] if row else 0

    def recover_queue(self, retention_hours: int = 48) -> Dict[str, Any]:
        """Settle queue rows left behind by a process that died.

        Rows still 'queued' are deliberately untouched - surviving a restart is
        the entire point of writing the queue down, and a worker will claim them
        in a moment.

        Rows marked 'running' belonged to a worker in the previous process. That
        coroutine is gone and will never update anything again. They are *not*
        re-queued: the workflow writes scene rows under deterministic ids
        ({story_id}_scene_{n}), so a second run collides on the primary key, and
        any images already paid for at RunPod would be paid for twice. Failing
        them and refunding the credit is the honest outcome. The caller does the
        refund, and must do so before the separate orphaned-story reconciler
        runs, otherwise the same job is refunded twice.
        """
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT q.story_id, q.user_id, s.status AS story_status
                FROM generation_queue q
                LEFT JOIN stories s ON s.story_id = q.story_id
                WHERE q.state = 'running'
            """).fetchall()
            abandoned = [dict(row) for row in rows]

            if abandoned:
                ids = [job["story_id"] for job in abandoned]
                marks = ",".join("?" * len(ids))
                conn.execute(f"""
                    UPDATE generation_queue
                    SET state = 'failed',
                        error = 'abandoned - process restarted',
                        finished_at = CURRENT_TIMESTAMP
                    WHERE story_id IN ({marks})
                """, ids)
                conn.execute(f"""
                    UPDATE stories
                    SET status = 'failed', updated_at = CURRENT_TIMESTAMP
                    WHERE story_id IN ({marks})
                      AND status IN ('initializing', 'processing')
                """, ids)

            still_queued = conn.execute("""
                SELECT COUNT(*) AS c FROM generation_queue WHERE state = 'queued'
            """).fetchone()["c"]

        pruned = self.prune_queue(retention_hours)
        return {
            "requeued": still_queued,
            "abandoned": len(abandoned),
            # Only jobs whose story was actually mid-flight owe a refund; one
            # that had already reached 'completed' or 'failed' does not.
            "abandoned_jobs": [
                job for job in abandoned
                if job.get("story_status") in ("initializing", "processing")
            ],
            "pruned": pruned,
        }

    def prune_queue(self, retention_hours: int = 48) -> int:
        """Drop settled rows past their retention window."""
        with self._get_conn() as conn:
            cursor = conn.execute("""
                DELETE FROM generation_queue
                WHERE state IN ('done', 'failed')
                  AND finished_at IS NOT NULL
                  AND finished_at < datetime('now', '-' || ? || ' hours')
            """, (retention_hours,))
            return cursor.rowcount


# Global instance
job_manager = JobStateManager()

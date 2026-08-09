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
                    quiz TEXT,
                    error TEXT,
                    quiz_size INTEGER,
                    attempt INTEGER DEFAULT 1
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
                if 'key_points' not in columns:
                    # JSON array of short revision notes shown on the summary
                    # screen between the last scene and the quiz. Nullable on
                    # purpose: every story generated before this column existed
                    # has none, and the player simply skips the screen for them.
                    conn.execute("ALTER TABLE stories ADD COLUMN key_points TEXT")
                if 'error' not in columns:
                    # mark_story_failed() writes here. Before this column existed
                    # the error message it was given was silently discarded (the
                    # UPDATE targeted a column that didn't exist) - every failure
                    # reason (content_unsuitable, extraction failure, etc.) was
                    # lost, and the frontend always fell back to its generic
                    # "AI Generation failed." message regardless of cause.
                    conn.execute("ALTER TABLE stories ADD COLUMN error TEXT")
                if 'quiz_size' not in columns:
                    # How many quiz questions the user asked for at upload. Needed
                    # AFTER generation too: the status endpoint compares it against
                    # what was actually produced to tell the user their quiz came
                    # up short, instead of silently handing over fewer questions.
                    conn.execute("ALTER TABLE stories ADD COLUMN quiz_size INTEGER")
                if 'attempt' not in columns:
                    # Which generation attempt is currently running. Surfaced by
                    # /api/status so the client can say "trying once more" instead
                    # of showing ~60s of unexplained dead air during the retry.
                    # Deliberately NOT a new `status` value: the client switches on
                    # status, and inventing one there would break every existing
                    # branch that expects initializing/processing/completed/failed.
                    conn.execute("ALTER TABLE stories ADD COLUMN attempt INTEGER DEFAULT 1")
                # Quality-pipeline scores (see StoryService.process_file_to_story
                # and _score_story) - admin-only visibility, never exposed on any
                # user-facing route. missing_items/unsupported_claims/
                # uncited_questions are JSON-encoded TEXT, same pattern as the
                # existing `quiz` column above.
                if 'coverage_score' not in columns:
                    conn.execute("ALTER TABLE stories ADD COLUMN coverage_score REAL")
                if 'faithfulness_score' not in columns:
                    conn.execute("ALTER TABLE stories ADD COLUMN faithfulness_score REAL")
                if 'hallucination_score' not in columns:
                    conn.execute("ALTER TABLE stories ADD COLUMN hallucination_score REAL")
                if 'citation_accuracy_score' not in columns:
                    conn.execute("ALTER TABLE stories ADD COLUMN citation_accuracy_score REAL")
                if 'overall_score' not in columns:
                    conn.execute("ALTER TABLE stories ADD COLUMN overall_score REAL")
                if 'gates_passed' not in columns:
                    conn.execute("ALTER TABLE stories ADD COLUMN gates_passed INTEGER")
                if 'quality_attempt_count' not in columns:
                    conn.execute("ALTER TABLE stories ADD COLUMN quality_attempt_count INTEGER")
                if 'missing_items' not in columns:
                    conn.execute("ALTER TABLE stories ADD COLUMN missing_items TEXT")
                if 'unsupported_claims' not in columns:
                    conn.execute("ALTER TABLE stories ADD COLUMN unsupported_claims TEXT")
                if 'uncited_questions' not in columns:
                    conn.execute("ALTER TABLE stories ADD COLUMN uncited_questions TEXT")
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

            # One row per story - unlike generation_queue this isn't N jobs per
            # story, it's a single video render that either hasn't happened,
            # is in flight, or has a result. Lives in this same file for the
            # same reason generation_queue does: it needs the same WAL settings
            # and the same BEGIN IMMEDIATE claim discipline as everything else
            # here, since both backend blue/green processes poll it.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS story_videos (
                    story_id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'queued',
                    progress_scene INTEGER DEFAULT 0,
                    total_scenes INTEGER,
                    error TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_story_videos_status
                ON story_videos(status, created_at)
            """)
    
    def initialize_story(self, story_id: str, grade_level: str, file_hash: Optional[str] = None, user_id: Optional[int] = None, username: Optional[str] = None, quiz_size: Optional[int] = None):
        """Create a preliminary story job record."""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO stories (story_id, status, title, grade_level, total_scenes, completed_scenes, file_hash, user_id, username, quiz_size)
                VALUES (?, 'initializing', 'Initializing story...', ?, 0, 0, ?, ?, ?, ?)
            """, (story_id, grade_level, file_hash, user_id, username, quiz_size))

    def update_story_metadata(
        self,
        story_id: str,
        title: str,
        total_scenes: int,
        quiz: Optional[List[Dict]] = None,
        quality_scores: Optional[Dict] = None,
        key_points: Optional[List[str]] = None,
    ):
        """Update story metadata after initial AI processing.

        quality_scores is the dict StoryService.process_file_to_story returns
        as its second value (never nested inside the story JSON itself - see
        that method's docstring for why). Optional and written in a separate
        UPDATE so a None here (scoring failed soft, or an older caller) never
        blocks the title/scenes/quiz update it was previously coupled to.
        """
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

            # Separate UPDATE for the same reason quality_scores gets one: the
            # model can return a story with no usable key_points, and that must
            # not stop the title/scenes/quiz write it would otherwise share.
            if key_points:
                conn.execute("""
                    UPDATE stories SET key_points = ? WHERE story_id = ?
                """, (json.dumps(key_points), story_id))

            if quality_scores is not None:
                conn.execute("""
                    UPDATE stories
                    SET coverage_score = ?, faithfulness_score = ?, hallucination_score = ?,
                        citation_accuracy_score = ?, overall_score = ?, gates_passed = ?,
                        quality_attempt_count = ?, missing_items = ?, unsupported_claims = ?,
                        uncited_questions = ?
                    WHERE story_id = ?
                """, (
                    quality_scores.get("coverage"),
                    quality_scores.get("faithfulness"),
                    quality_scores.get("hallucination"),
                    quality_scores.get("citation_accuracy"),
                    quality_scores.get("overall"),
                    quality_scores.get("gates_passed"),
                    quality_scores.get("attempt"),
                    json.dumps(quality_scores.get("missing_items") or []),
                    json.dumps(quality_scores.get("unsupported_claims") or []),
                    json.dumps(quality_scores.get("uncited_questions") or []),
                    story_id,
                ))

    def get_quality_scores(self, story_ids: List[str]) -> Dict[str, Dict]:
        """Batch-fetch quality scores for the admin panel: story_id -> scores
        dict, or absent when a story predates this feature or scoring never
        ran. One query for the whole admin list, not one per row."""
        if not story_ids:
            return {}
        with self._get_conn() as conn:
            placeholders = ",".join("?" for _ in story_ids)
            cursor = conn.execute(f"""
                SELECT story_id, coverage_score, faithfulness_score, hallucination_score,
                       citation_accuracy_score, overall_score, gates_passed,
                       quality_attempt_count, missing_items, unsupported_claims, uncited_questions
                FROM stories
                WHERE story_id IN ({placeholders}) AND overall_score IS NOT NULL
            """, story_ids)
            result = {}
            for row in cursor.fetchall():
                result[row["story_id"]] = {
                    "coverage": row["coverage_score"],
                    "faithfulness": row["faithfulness_score"],
                    "hallucination": row["hallucination_score"],
                    "citation_accuracy": row["citation_accuracy_score"],
                    "overall": row["overall_score"],
                    "gates_passed": row["gates_passed"],
                    "attempt": row["quality_attempt_count"],
                    "missing_items": json.loads(row["missing_items"]) if row["missing_items"] else [],
                    "unsupported_claims": json.loads(row["unsupported_claims"]) if row["unsupported_claims"] else [],
                    "uncited_questions": json.loads(row["uncited_questions"]) if row["uncited_questions"] else [],
                }
            return result
    
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

    def get_story_ids_for_user(self, user_id: int) -> List[str]:
        """Every story_id this user owns, in progress or finished.

        Account deletion needs this because story assets live on disk under
        their story_id and there is no other way to find them once the user
        row is gone.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT story_id FROM stories WHERE user_id = ?", (user_id,)
            ).fetchall()
        return [r["story_id"] for r in rows]

    def has_active_job(self, user_id: int) -> bool:
        """True while any of this user's stories is queued or being generated.

        A worker mid-generation holds open paths under generated_stories/<id>.
        Deleting that tree out from under it produces half-written scenes and a
        traceback per remaining scene, so callers that destroy data must refuse
        while this is true rather than race it.

        Staleness matters as much as status. A story left 'processing' by a
        worker that died is indistinguishable by status alone from one being
        written to right now - and if that counted as active forever, a single
        dead job would permanently block the owner from deleting their account.
        Anything untouched for longer than the generation timeout cannot still
        be running: the queue's own watchdog would have reclaimed it. The window
        is read from the same env var the queue uses (services/job_queue.py) so
        the two cannot drift apart.
        """
        timeout_s = float(os.getenv("GENERATION_TIMEOUT_SECONDS", "1800"))
        cutoff = f"-{timeout_s} seconds"
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT COUNT(*) AS c FROM stories
                WHERE user_id = ?
                  AND status IN ('initializing', 'processing')
                  AND updated_at > datetime('now', ?)
            """, (user_id, cutoff)).fetchone()
            if row["c"]:
                return True
            row = conn.execute("""
                SELECT COUNT(*) AS c FROM generation_queue
                WHERE user_id = ?
                  AND (
                        state = 'queued'
                     OR (state = 'running' AND started_at > datetime('now', ?))
                  )
            """, (user_id, cutoff)).fetchone()
            return bool(row["c"])

    def delete_all_for_user(self, user_id: int) -> int:
        """Delete every story, scene and queue row belonging to a user.

        Returns the number of story rows removed. These live in SQLite while
        the users table lives in MySQL - there is no foreign key between the
        two databases, so nothing cascades here and this must be called
        explicitly or the rows orphan forever.
        """
        story_ids = self.get_story_ids_for_user(user_id)
        with self._get_conn() as conn:
            for story_id in story_ids:
                conn.execute("DELETE FROM scenes WHERE story_id = ?", (story_id,))
            conn.execute("DELETE FROM generation_queue WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM stories WHERE user_id = ?", (user_id,))
        return len(story_ids)

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
                SET status = 'failed', updated_at = CURRENT_TIMESTAMP, error = ?
                WHERE story_id = ?
            """, (error, story_id))

    def mark_story_retrying(self, story_id: str, attempt: int = 2):
        """Record that generation is on its second attempt.

        Status is intentionally left alone - see the `attempt` column migration.
        """
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE stories
                SET attempt = ?, updated_at = CURRENT_TIMESTAMP
                WHERE story_id = ?
            """, (attempt, story_id))

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

    # ==================================================================
    # Video render jobs
    # ==================================================================
    # See services/video_queue.py for the worker that drains this table and
    # services/video_service.py for the actual ffmpeg work.

    def get_video_status(self, story_id: str) -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM story_videos WHERE story_id = ?", (story_id,)
            ).fetchone()
            return dict(row) if row else None

    def enqueue_video(self, story_id: str, user_id: Optional[int], total_scenes: int) -> None:
        """(Re)queue a video render. INSERT OR REPLACE so retrying a failed
        render - or re-requesting after the story changed - starts clean
        instead of layering onto a stale progress/error from a previous
        attempt."""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO story_videos
                    (story_id, user_id, status, progress_scene, total_scenes, error, created_at, updated_at)
                VALUES (?, ?, 'queued', 0, ?, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """, (story_id, user_id, total_scenes))

    def claim_next_video(self) -> Optional[Dict]:
        """Same BEGIN IMMEDIATE atomic-claim pattern as claim_next_job above -
        both backend colors poll this table, and a plain SELECT-then-UPDATE
        would let both claim the same row and render the same story twice."""
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""
                SELECT * FROM story_videos
                WHERE status = 'queued'
                ORDER BY created_at
                LIMIT 1
            """).fetchone()
            if not row:
                return None
            conn.execute("""
                UPDATE story_videos
                SET status = 'processing', updated_at = CURRENT_TIMESTAMP
                WHERE story_id = ?
            """, (row["story_id"],))
            return dict(row)

    def update_video_progress(self, story_id: str, progress_scene: int) -> None:
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE story_videos
                SET progress_scene = ?, updated_at = CURRENT_TIMESTAMP
                WHERE story_id = ?
            """, (progress_scene, story_id))

    def finish_video(self, story_id: str, error: Optional[str] = None) -> None:
        """Settle a running video render. `error` set means it failed."""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE story_videos
                SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE story_id = ?
            """, ("failed" if error else "completed", error, story_id))

    def recover_stuck_videos(self) -> int:
        """Fail any row left 'processing' from before this process started -
        identical reasoning to reconcile_orphaned_jobs(): the coroutine that
        owned it died with the previous process and will never update it
        again. Returns the number of rows reclaimed."""
        with self._get_conn() as conn:
            cursor = conn.execute("""
                UPDATE story_videos
                SET status = 'failed', error = 'abandoned - process restarted', updated_at = CURRENT_TIMESTAMP
                WHERE status = 'processing'
            """)
            return cursor.rowcount


# Global instance
job_manager = JobStateManager()

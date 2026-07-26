"""
Database connection and utilities for MySQL
"""
import os
import time
import threading
import mysql.connector
from mysql.connector import pooling
from mysql.connector.errors import PoolError
from contextlib import contextmanager
from dotenv import load_dotenv
import logging
from typing import Optional, Dict, Any

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

# --- Environment Detection ---
ENV = os.getenv("ENV", "production")

# --- Pool sizing ---
# The pool size is a hard cap on simultaneous database work, because the
# pool has no wait queue (see _acquire_connection). 5 was enough for
# single-digit concurrency and nothing more. 32 is mysql-connector's own
# CNX_POOL_MAXSIZE ceiling; the server permits 151 connections in total, so
# this stays within budget even with several uvicorn worker processes.
POOL_SIZE = max(1, min(32, int(os.getenv("MYSQL_POOL_SIZE", "32"))))
POOL_ACQUIRE_ATTEMPTS = int(os.getenv("MYSQL_POOL_ACQUIRE_ATTEMPTS", "8"))
POOL_ACQUIRE_BACKOFF = float(os.getenv("MYSQL_POOL_ACQUIRE_BACKOFF", "0.05"))

# --- Database Configuration ---
# Skip DB config in development mode
DB_CONFIG: Optional[Dict[str, Any]] = None
if ENV == "development":
    logger.warning("🚫 DEVELOPMENT MODE: Database functionality disabled")
else:
    # Use os.getenv to read from environment, which is populated by Docker Compose
    DB_CONFIG = {
        "host": os.getenv("MYSQL_HOST"),
        "user": os.getenv("MYSQL_USER"),
        "password": os.getenv("MYSQL_PASSWORD"),
        "database": os.getenv("MYSQL_DATABASE"),
        "port": int(os.getenv("MYSQL_PORT", 3306)),
        "pool_name": "edusmart_pool",
        "pool_size": POOL_SIZE,
    }

connection_pool: Optional[pooling.MySQLConnectionPool] = None
# Guards pool construction only - not per-request checkout, which the pool
# already handles internally. See get_connection_pool for why this matters.
_pool_lock = threading.Lock()
_pool_last_failure = 0.0
POOL_RETRY_COOLDOWN = float(os.getenv("MYSQL_POOL_RETRY_COOLDOWN", "2.0"))

def get_connection_pool():
    """Initializes and returns the connection pool singleton.

    Construction is serialised. MySQLConnectionPool.__init__ eagerly opens
    pool_size connections, so without the lock every concurrent caller that
    saw an empty singleton built its own pool and the connection count
    multiplied by the number of callers - enough to exhaust the server's
    max_connections outright and fail all of them. The double check means the
    first caller builds it while the others wait, then everyone sees the same
    finished pool.
    """
    global connection_pool, _pool_last_failure

    # Skip in development mode
    if ENV == "development":
        return None

    # Fast path: pool already built, no lock needed.
    if connection_pool is not None:
        return connection_pool

    with _pool_lock:
        # Re-check inside the lock - another thread may have built it while
        # this one was waiting, and building a second pool is the whole bug.
        if connection_pool is not None:
            return connection_pool

        # An unreachable database must not turn every request into another
        # construction attempt; that is what turns an outage into a
        # connection storm the database cannot recover from.
        since_failure = time.monotonic() - _pool_last_failure
        if _pool_last_failure and since_failure < POOL_RETRY_COOLDOWN:
            raise ConnectionError(
                f"MySQL pool unavailable; retry cooling down "
                f"({POOL_RETRY_COOLDOWN - since_failure:.1f}s left)"
            )

        try:
            # Ensure DB_CONFIG is not None
            if DB_CONFIG is None:
                raise ValueError("Database configuration is not available")

            # Ensure all required config values are present
            for key in ["host", "user", "password", "database"]:
                if DB_CONFIG.get(key) is None:
                    raise ValueError(f"Missing required DB config: {key}")

            connection_pool = pooling.MySQLConnectionPool(**DB_CONFIG)
            _pool_last_failure = 0.0
            logger.info(
                f"✓ MySQL connection pool created ({POOL_SIZE} connections) "
                f"to {DB_CONFIG['host']}"
            )
        except (mysql.connector.Error, ValueError) as err:
            _pool_last_failure = time.monotonic()
            logger.error(f"⚠ Failed to create MySQL connection pool: {err}")
            # Log details without showing password
            if DB_CONFIG:
                config_details = {k: v for k, v in DB_CONFIG.items() if k != 'password'}
                logger.error(f"   Using config: {config_details}")
            # Set pool to None so subsequent calls don't succeed
            connection_pool = None
            raise
        return connection_pool

def _acquire_connection(pool):
    """Take a connection from the pool, waiting briefly if it is momentarily full.

    MySQLConnectionPool has no wait queue: get_connection() raises PoolError
    the instant every connection is checked out. Under real concurrency that
    turns a contention window of a few hundred milliseconds into a hard 500
    for whichever request arrived last. Retrying with a short linear backoff
    converts it into a brief wait, and still fails fast if the pool is
    genuinely saturated for the whole window rather than hanging forever.
    """
    last_err = None
    for attempt in range(POOL_ACQUIRE_ATTEMPTS):
        try:
            return pool.get_connection()
        except PoolError as err:
            last_err = err
            time.sleep(POOL_ACQUIRE_BACKOFF * (attempt + 1))
    logger.error(
        f"MySQL pool ({POOL_SIZE} connections) exhausted after "
        f"{POOL_ACQUIRE_ATTEMPTS} attempts - raise MYSQL_POOL_SIZE"
    )
    raise last_err


@contextmanager
def get_db_cursor(commit=False):
    """
    Provides a database cursor from the connection pool.
    Handles connection acquisition, cursor creation, and commit/rollback.
    """
    # Skip in development mode
    if ENV == "development":
        raise ConnectionError("Database unavailable in development mode")

    pool = get_connection_pool()
    if pool is None:
        raise ConnectionError("Database connection pool is not available.")

    connection = None
    cursor = None
    try:
        connection = _acquire_connection(pool)
        cursor = connection.cursor(dictionary=True)
        yield cursor
        if commit:
            connection.commit()
    except mysql.connector.Error as err:
        if connection:
            connection.rollback()
        logger.error(f"Database Error: {err}")
        raise  # Re-raise the exception to be handled by the caller
    except Exception:
        # Any NON-database exception raised inside the `with` body - e.g. an
        # HTTPException from a validation check - must roll back too. Previously
        # it skipped both the commit and the rollback, so a half-finished
        # transaction (and any FOR UPDATE locks it was holding) was handed back
        # to the pool still open, to be inherited by the next request.
        if connection:
            connection.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()

# --- Schema Definition ---
# Maps table names to their CREATE TABLE statements
TABLES: Dict[str, str] = {}

TABLES['users'] = """
    CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        email VARCHAR(255) UNIQUE NOT NULL,
        username VARCHAR(100) UNIQUE NOT NULL,
        password_hash VARCHAR(255) NULL,
        auth_provider VARCHAR(20) NOT NULL DEFAULT 'local',
        provider_user_id VARCHAR(255) NULL,
        is_verified BOOLEAN DEFAULT FALSE,
        is_premium BOOLEAN DEFAULT FALSE,
        is_admin BOOLEAN DEFAULT FALSE,
        last_verification_sent TIMESTAMP NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_email (email),
        INDEX idx_username (username),
        UNIQUE INDEX idx_provider_identity (auth_provider, provider_user_id),
        CONSTRAINT chk_local_has_password
            CHECK (auth_provider <> 'local' OR password_hash IS NOT NULL),
        CONSTRAINT chk_has_some_credential
            CHECK (password_hash IS NOT NULL OR provider_user_id IS NOT NULL)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

TABLES['email_verifications'] = """
    CREATE TABLE IF NOT EXISTS email_verifications (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        token VARCHAR(255) UNIQUE NOT NULL,
        expires_at TIMESTAMP NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        INDEX idx_token (token)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

TABLES['password_reset_tokens'] = """
    CREATE TABLE IF NOT EXISTS password_reset_tokens (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        token VARCHAR(255) UNIQUE NOT NULL,
        expires_at TIMESTAMP NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        INDEX idx_token (token),
        INDEX idx_user_id (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

TABLES['user_stories'] = """
    CREATE TABLE IF NOT EXISTS user_stories (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        story_id VARCHAR(255) UNIQUE NOT NULL,
        name VARCHAR(255) NOT NULL,
        story_data JSON NOT NULL,
        is_public BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        INDEX idx_is_public (is_public)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

TABLES['subscription_plans'] = """
    CREATE TABLE IF NOT EXISTS subscription_plans (
        tier_key VARCHAR(50) PRIMARY KEY,
        display_name VARCHAR(100) NOT NULL,
        price_display VARCHAR(20) NOT NULL,
        stripe_price_id VARCHAR(255) NULL,
        credits_included INT NOT NULL,
        billing_mode ENUM('subscription', 'one_time') NOT NULL,
        is_active BOOLEAN DEFAULT TRUE,
        sort_order INT DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

TABLES['credit_transactions'] = """
    CREATE TABLE IF NOT EXISTS credit_transactions (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        delta INT NOT NULL,
        reason VARCHAR(50) NOT NULL,
        story_id VARCHAR(255) NULL,
        stripe_event_id VARCHAR(255) NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        INDEX idx_user_id (user_id),
        INDEX idx_stripe_event_id (stripe_event_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

TABLES['promo_codes'] = """
    CREATE TABLE IF NOT EXISTS promo_codes (
        code VARCHAR(50) PRIMARY KEY,
        discount_type ENUM('percent_off', 'free_credits') NOT NULL,
        discount_value INT NOT NULL,
        stripe_coupon_id VARCHAR(255) NULL,
        applies_to_tier VARCHAR(50) NULL,
        max_redemptions INT NULL,
        max_redemptions_per_user INT DEFAULT 1,
        times_redeemed INT DEFAULT 0,
        expires_at TIMESTAMP NULL,
        is_active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

TABLES['webhook_events'] = """
    CREATE TABLE IF NOT EXISTS webhook_events (
        event_id VARCHAR(255) PRIMARY KEY,
        event_type VARCHAR(100) NOT NULL,
        received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

TABLES['promo_redemptions'] = """
    CREATE TABLE IF NOT EXISTS promo_redemptions (
        id INT AUTO_INCREMENT PRIMARY KEY,
        code VARCHAR(50) NOT NULL,
        user_id INT NOT NULL,
        credits_granted INT NULL,
        stripe_checkout_session_id VARCHAR(255) NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (code) REFERENCES promo_codes(code) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        INDEX idx_code_user (code, user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

# Default subscription tiers, seeded on first run only (see initialize_database).
# All pricing after this point is live-editable in the DB, not in code.
DEFAULT_SUBSCRIPTION_PLANS = [
    ("free", "Free", "Free", None, 2, "subscription", 0,
     "Try it out - perfect for a first story night.",
     "Up to 5 scenes per story\nCommunity support",
     False),
    ("starter", "Starter", "$6.99/mo", None, 15, "subscription", 1,
     "For regular storytime, a new story every couple of days.",
     "Full-length stories, no scene cap\nEmail support\nCancel anytime",
     False),
    ("family", "Family / Classroom", "$14.99/mo", None, 40, "subscription", 2,
     "For families and classrooms who want more, plus offline access.",
     "Offline ZIP export\nPriority generation queue\nPriority support",
     True),
    ("topup_10", "10 Story Top-Up", "$4.99", None, 10, "one_time", 3,
     "Ran out mid-month? Grab 10 more, no subscription needed.",
     "Never expires\nOne-time payment",
     False),
]

def initialize_database():
    """Creates all tables defined in the TABLES dictionary."""
    try:
        # Commit=True because we are executing DDL (Data Definition Language)
        with get_db_cursor(commit=True) as cursor:
            logger.info("Initializing database schema...")
            for table_name, table_description in TABLES.items():
                logger.info(f"Creating table '{table_name}'...")
                cursor.execute(table_description)
            logger.info("✓ Database schema initialized successfully")

            # --- Schema Migration: Add 'is_admin' column if it doesn't exist ---
            logger.info("Checking for necessary schema migrations...")
            cursor.execute("""
                SELECT COUNT(*) AS count
                FROM information_schema.columns
                WHERE table_schema = DATABASE()
                AND table_name = 'users'
                AND column_name = 'is_admin'
            """)
            if cursor.fetchone()['count'] == 0:
                logger.warning("! 'is_admin' column not found in 'users' table. Adding it now...")
                cursor.execute("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT FALSE")
                logger.info("✓ Successfully added 'is_admin' column to 'users' table.")
            else:
                logger.info("✓ 'users' table schema is up to date.")

            # --- Schema Migration: Add 'quiz_completed' column to user_stories ---
            cursor.execute("""
                SELECT COUNT(*) AS count
                FROM information_schema.columns
                WHERE table_schema = DATABASE()
                AND table_name = 'user_stories'
                AND column_name = 'quiz_completed'
            """)
            if cursor.fetchone()['count'] == 0:
                logger.warning("! 'quiz_completed' column not found in 'user_stories' table. Adding it now...")
                cursor.execute("ALTER TABLE user_stories ADD COLUMN quiz_completed BOOLEAN DEFAULT FALSE")
                logger.info("✓ Successfully added 'quiz_completed' column to 'user_stories' table.")
            else:
                logger.info("✓ 'user_stories' table schema is up to date.")

            # --- Schema Migration: Add 'is_public' column to user_stories ---
            # Consent to be discoverable by other users. Defaults to FALSE, so
            # every story that existed before this column stays private: consent
            # is something a user gives, never something a migration assumes.
            cursor.execute("""
                SELECT COUNT(*) AS count
                FROM information_schema.columns
                WHERE table_schema = DATABASE()
                AND table_name = 'user_stories'
                AND column_name = 'is_public'
            """)
            if cursor.fetchone()['count'] == 0:
                logger.warning("! 'is_public' column not found in 'user_stories' table. Adding it now...")
                cursor.execute("ALTER TABLE user_stories ADD COLUMN is_public BOOLEAN NOT NULL DEFAULT FALSE")
                cursor.execute("ALTER TABLE user_stories ADD INDEX idx_is_public (is_public)")
                logger.info("✓ Successfully added 'is_public' column to 'user_stories' table.")

            # --- Schema Migration: Add billing columns to 'users' ---
            billing_columns = {
                "stripe_customer_id": "VARCHAR(255) NULL",
                "subscription_tier": "VARCHAR(50) DEFAULT 'free'",
                "subscription_status": "VARCHAR(50) DEFAULT 'inactive'",
                "stripe_subscription_id": "VARCHAR(255) NULL",
                "credits_balance": "INT DEFAULT 2",
                "credits_reset_at": "TIMESTAMP NULL",
            }
            for column_name, column_def in billing_columns.items():
                cursor.execute("""
                    SELECT COUNT(*) AS count
                    FROM information_schema.columns
                    WHERE table_schema = DATABASE()
                    AND table_name = 'users'
                    AND column_name = %s
                """, (column_name,))
                if cursor.fetchone()['count'] == 0:
                    logger.warning(f"! '{column_name}' column not found in 'users' table. Adding it now...")
                    cursor.execute(f"ALTER TABLE users ADD COLUMN {column_name} {column_def}")
                    logger.info(f"✓ Successfully added '{column_name}' column to 'users' table.")
            logger.info("✓ 'users' billing columns are up to date.")

            # --- Schema Migration: Add plan copy/badge columns to 'subscription_plans' ---
            plan_columns = {
                "description": "VARCHAR(255) NULL",
                "features": "TEXT NULL",
                "is_recommended": "BOOLEAN DEFAULT FALSE",
            }
            for column_name, column_def in plan_columns.items():
                cursor.execute("""
                    SELECT COUNT(*) AS count
                    FROM information_schema.columns
                    WHERE table_schema = DATABASE()
                    AND table_name = 'subscription_plans'
                    AND column_name = %s
                """, (column_name,))
                if cursor.fetchone()['count'] == 0:
                    logger.warning(f"! '{column_name}' column not found in 'subscription_plans' table. Adding it now...")
                    cursor.execute(f"ALTER TABLE subscription_plans ADD COLUMN {column_name} {column_def}")
                    logger.info(f"✓ Successfully added '{column_name}' column to 'subscription_plans' table.")
            logger.info("✓ 'subscription_plans' copy columns are up to date.")

            # --- Seed default subscription plans (idempotent, first-run only) ---
            for tier_key, display_name, price_display, stripe_price_id, credits_included, billing_mode, sort_order, description, features, is_recommended in DEFAULT_SUBSCRIPTION_PLANS:
                cursor.execute(
                    """INSERT IGNORE INTO subscription_plans
                       (tier_key, display_name, price_display, stripe_price_id, credits_included, billing_mode, sort_order, description, features, is_recommended)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (tier_key, display_name, price_display, stripe_price_id, credits_included, billing_mode, sort_order, description, features, is_recommended)
                )
                # Backfill copy for plans that were seeded before this feature existed.
                cursor.execute(
                    "UPDATE subscription_plans SET description = %s, features = %s, is_recommended = %s WHERE tier_key = %s AND description IS NULL",
                    (description, features, is_recommended, tier_key)
                )
            logger.info("✓ Default subscription plans seeded/backfilled (existing pricing left untouched).")

    except (mysql.connector.Error, ConnectionError) as err:
        logger.error(f"⚠ Could not initialize database: {err}")
        # This is a critical failure on startup, so re-raise
        raise

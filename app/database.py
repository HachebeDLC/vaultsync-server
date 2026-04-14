import logging
import time
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from .config import DB_HOST, DB_NAME, DB_USER, DB_PASS

logger = logging.getLogger("VaultSync")

_db_pool = None

def get_pool():
    global _db_pool
    if _db_pool is not None:
        return _db_pool
        
    retries = 5
    while retries > 0:
        try:
            _db_pool = pool.ThreadedConnectionPool(
                5, 20, 
                host=DB_HOST, 
                database=DB_NAME, 
                user=DB_USER, 
                password=DB_PASS
            )
            logger.info("✅ Database connection pool initialized")
            return _db_pool
        except Exception as e:
            retries -= 1
            logger.warning(f"⚠️ Could not initialize DB pool (Retries left: {retries}): {e}")
            if retries == 0:
                logger.error("❌ Database pool initialization failed after all retries.")
                raise e
            time.sleep(2)

@contextmanager
def get_db():
    pool_obj = get_pool()
    conn = pool_obj.getconn()
    try:
        yield conn
    finally:
        pool_obj.putconn(conn)

def _create_tables(cursor) -> None:
    """Create all tables if they do not already exist."""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            username TEXT,
            salt TEXT,
            recovery_payload TEXT,
            recovery_salt TEXT,
            created_at BIGINT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS files (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            path TEXT NOT NULL,
            hash TEXT,
            size BIGINT,
            updated_at BIGINT,
            device_name TEXT,
            blocks JSONB,
            UNIQUE(user_id, path)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            token TEXT UNIQUE NOT NULL,
            expires_at BIGINT NOT NULL,
            created_at BIGINT NOT NULL,
            revoked BOOLEAN DEFAULT FALSE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_refresh_token ON refresh_tokens (token)')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS romm_games (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            romm_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            fs_name TEXT,
            platform_slug TEXT,
            UNIQUE(user_id, romm_id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_romm_games_user_id ON romm_games (user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_romm_games_name ON romm_games USING gin(name gin_trgm_ops)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_romm_games_fs_name ON romm_games USING gin(fs_name gin_trgm_ops)')
    
    # We must ensure the pg_trgm extension is created for ILIKE performance
    cursor.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')


def _col_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
        (table, column),
    )
    return cursor.fetchone() is not None


def _run_migrations(cursor) -> None:
    """Apply all pending schema migrations (idempotent)."""
    # Drop vestigial encryption_key (key is derived client-side via PBKDF2)
    if _col_exists(cursor, "users", "encryption_key"):
        logger.info("Migrating: Dropping unused 'encryption_key' from 'users'")
        cursor.execute("ALTER TABLE users DROP COLUMN encryption_key")

    if not _col_exists(cursor, "users", "salt"):
        logger.info("Migrating: Adding 'salt' to 'users'")
        cursor.execute("ALTER TABLE users ADD COLUMN salt TEXT")

    if not _col_exists(cursor, "users", "recovery_payload"):
        logger.info("Migrating: Adding recovery columns to 'users'")
        cursor.execute("ALTER TABLE users ADD COLUMN recovery_payload TEXT")
        cursor.execute("ALTER TABLE users ADD COLUMN recovery_salt TEXT")

    if not _col_exists(cursor, "users", "romm_url"):
        logger.info("Migrating: Adding RomM columns to 'users'")
        cursor.execute("ALTER TABLE users ADD COLUMN romm_url TEXT")
        cursor.execute("ALTER TABLE users ADD COLUMN romm_api_key TEXT")

    # Migrate files.blocks TEXT → JSONB
    cursor.execute(
        "SELECT data_type FROM information_schema.columns WHERE table_name='files' AND column_name='blocks'"
    )
    col = cursor.fetchone()
    if col and col[0].lower() == "text":
        logger.info("Migrating: Converting files.blocks from TEXT to JSONB")
        cursor.execute("""
            ALTER TABLE files
            ALTER COLUMN blocks TYPE JSONB
            USING CASE WHEN blocks IS NULL OR blocks = '' THEN NULL ELSE blocks::jsonb END
        """)


def init_db():
    """Initializes the database schema with migration guards."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            _create_tables(cursor)
            _run_migrations(cursor)
            conn.commit()
            logger.info("✅ Database schema is up to date")
    except Exception as e:
        logger.error(f"❌ DB Init Error: {e}")
        raise

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

def init_db():
    """Initializes the database schema with migration guards."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Users table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY, 
                    email TEXT UNIQUE NOT NULL, 
                    password_hash TEXT NOT NULL, 
                    username TEXT, 
                    encryption_key TEXT, 
                    salt TEXT, 
                    recovery_payload TEXT, 
                    recovery_salt TEXT, 
                    created_at BIGINT
                )
            ''')
            
            # Migration check: salt column
            cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='salt'")
            if not cursor.fetchone():
                logger.info("Migrating: Adding 'salt' to 'users'")
                cursor.execute("ALTER TABLE users ADD COLUMN salt TEXT")
                
            # Migration check: recovery columns
            cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='recovery_payload'")
            if not cursor.fetchone():
                logger.info("Migrating: Adding recovery columns to 'users'")
                cursor.execute("ALTER TABLE users ADD COLUMN recovery_payload TEXT")
                cursor.execute("ALTER TABLE users ADD COLUMN recovery_salt TEXT")
            
            # Files table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS files (
                    id SERIAL PRIMARY KEY, 
                    user_id INTEGER REFERENCES users(id), 
                    path TEXT NOT NULL, 
                    hash TEXT, 
                    size BIGINT, 
                    updated_at BIGINT, 
                    device_name TEXT, 
                    blocks TEXT, 
                    UNIQUE(user_id, path)
                )
            ''')
            # Priority 8: Add index for path-prefix queries
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_files_path ON files (path)')
            conn.commit()
            logger.info("✅ Database schema is up to date")
    except Exception as e:
        logger.error(f"❌ DB Init Error: {e}")
        raise

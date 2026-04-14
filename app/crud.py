import time
from psycopg2.extras import RealDictCursor, Json
from cachetools import TTLCache

from threading import Lock
file_metadata_cache = TTLCache(maxsize=10000, ttl=300)
_cache_lock = Lock()

def get_user_by_email(conn, email: str):
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
    return cursor.fetchone()

def create_user(conn, email: str, password_hash: str, username: str, salt: str):
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (email, password_hash, username, salt, created_at) VALUES (%s, %s, %s, %s, %s) RETURNING id", 
        (email, password_hash, username, salt, int(time.time()))
    )
    return cursor.fetchone()[0]

def update_user_recovery(conn, user_id: int, recovery_payload: str, recovery_salt: str):
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET recovery_payload = %s, recovery_salt = %s WHERE id = %s", 
        (recovery_payload, recovery_salt, user_id)
    )

def get_recovery_info(conn, email: str):
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute(
        "SELECT recovery_payload, recovery_salt FROM users WHERE email = %s", 
        (email,)
    )
    return cursor.fetchone()

def list_user_files(conn, user_id: int, prefix: str = None, limit: int = 200, after: str = None):
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    params = [user_id]
    conditions = ["user_id = %s"]

    if prefix:
        conditions.append("path ILIKE %s")
        params.append(f"{prefix}%")

    if after:
        conditions.append("path > %s")
        params.append(after)

    query = (
        f"SELECT path, hash, size, updated_at, device_name FROM files"
        f" WHERE {' AND '.join(conditions)}"
        f" ORDER BY path LIMIT %s"
    )
    params.append(limit + 1)  # fetch one extra to determine if there's a next page
    cursor.execute(query, params)
    rows = cursor.fetchall()
    
    next_cursor = None
    if len(rows) > limit:
        next_cursor = rows[limit - 1]['path']
        rows = rows[:limit]
        
    return rows, next_cursor

def get_file_metadata(conn, user_id: int, path: str):
    cache_key = f"{user_id}:{path}"
    with _cache_lock:
        if cache_key in file_metadata_cache:
            # We cache metadata WITHOUT the large blocks JSON to save memory
            # The database result will still be fetched below if a full record is needed
            cached_result = dict(file_metadata_cache[cache_key])

            # Since blocks might be needed by the caller, we must decide if we fetch it separately
            # or if we only cache the core metadata.
            # Looking at routers/files.py, many callers don't need blocks immediately.
            # However, finalize_upload and get_file_manifest DO need it.
            # For this refactor, we will cache ONLY metadata and fetch from DB if blocks is missing.
            return cached_result

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute(
        "SELECT hash, size, updated_at, device_name, blocks FROM files WHERE user_id = %s AND path = %s",
        (user_id, path)
    )
    result = cursor.fetchone()
    if result:
        # Cache a copy WITHOUT blocks
        metadata_only = dict(result)
        metadata_only.pop('blocks', None)
        with _cache_lock:
            file_metadata_cache[cache_key] = metadata_only
    return result

def upsert_file_metadata(conn, user_id: int, path: str, hash: str, size: int, updated_at: int, device_name: str, blocks: list):
    with _cache_lock:
        file_metadata_cache.pop(f"{user_id}:{path}", None)
    cursor = conn.cursor()
    cursor.execute(
        '''INSERT INTO files (user_id, path, hash, size, updated_at, device_name, blocks)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT(user_id, path) DO UPDATE SET
           hash=EXCLUDED.hash, size=EXCLUDED.size, updated_at=EXCLUDED.updated_at,
           device_name=EXCLUDED.device_name, blocks=EXCLUDED.blocks''',
        (user_id, path, hash, size, updated_at, device_name, Json(blocks))
    )

def update_file_sync(conn, user_id: int, path: str, hash: str, size: int, updated_at: int, blocks: list):
    with _cache_lock:
        file_metadata_cache.pop(f"{user_id}:{path}", None)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE files SET hash=%s, size=%s, updated_at=%s, blocks=%s WHERE user_id=%s AND path=%s",
        (hash, size, updated_at, Json(blocks), user_id, path)
    )


def delete_file_metadata(conn, user_id: int, path: str):
    with _cache_lock:
        file_metadata_cache.pop(f"{user_id}:{path}", None)
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM files WHERE user_id = %s AND path = %s", 
        (user_id, path)
    )

def create_refresh_token(conn, user_id: int, token: str, expires_at: int):
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO refresh_tokens (user_id, token, expires_at, created_at) VALUES (%s, %s, %s, %s)",
        (user_id, token, expires_at, int(time.time()))
    )

def get_refresh_token(conn, token: str):
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute(
        "SELECT * FROM refresh_tokens WHERE token = %s AND revoked = FALSE",
        (token,)
    )
    return cursor.fetchone()

def revoke_refresh_token(conn, token: str):
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE refresh_tokens SET revoked = TRUE WHERE token = %s",
        (token,)
    )

def revoke_all_user_refresh_tokens(conn, user_id: int):
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE refresh_tokens SET revoked = TRUE WHERE user_id = %s",
        (user_id,)
    )

def update_user_romm_creds(conn, user_id: int, romm_url: str, romm_api_key: str):
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET romm_url = %s, romm_api_key = %s WHERE id = %s", 
        (romm_url, romm_api_key, user_id)
    )

def sync_user_romm_library(conn, user_id: int, games_list: list):
    """Replaces the user's cached RomM library with a fresh list."""
    cursor = conn.cursor()
    cursor.execute("DELETE FROM romm_games WHERE user_id = %s", (user_id,))
    
    if not games_list:
        return
        
    from psycopg2.extras import execute_values
    query = """
        INSERT INTO romm_games (user_id, romm_id, name, fs_name, platform_slug)
        VALUES %s
    """
    
    # We use a set to avoid duplicate (user_id, romm_id) pairs if RomM API returns dupes
    seen_ids = set()
    data = []
    
    for g in games_list:
        romm_id = g.get('id')
        if not romm_id or romm_id in seen_ids:
            continue
            
        seen_ids.add(romm_id)
        name = g.get('name', '')
        fs_name = g.get('fs_name', '')
        platform = g.get('platform', {})
        platform_slug = platform.get('slug', '') if platform else ''
        
        data.append((user_id, romm_id, name, fs_name, platform_slug))
        
    execute_values(cursor, query, data)

def find_romm_game_for_user(conn, user_id: int, target_id: str, target_name: str, platform_slug: str = None):
    """Searches the user's localized RomM library for a match."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    # 1. Exact ID Match (for Switch, GC, PSP)
    if target_id:
        query = "SELECT romm_id, name FROM romm_games WHERE user_id = %s AND (name ILIKE %s OR fs_name ILIKE %s)"
        params = [user_id, f'%{target_id}%', f'%{target_id}%']
        
        if platform_slug:
            platform_query = query + " AND platform_slug = %s LIMIT 1"
            platform_params = params + [platform_slug]
            cursor.execute(platform_query, tuple(platform_params))
            res = cursor.fetchone()
            if res: return res['romm_id']
            
        fallback_query = query + " LIMIT 1"
        cursor.execute(fallback_query, tuple(params))
        res = cursor.fetchone()
        if res: return res['romm_id']
        
    # 2. Fuzzy Name Match
    if target_name:
        clean_target = target_name.lower().strip()
        
        query = "SELECT romm_id, name FROM romm_games WHERE user_id = %s AND (name ILIKE %s OR fs_name ILIKE %s)"
        params = [user_id, f'%{clean_target}%', f'%{clean_target}%']
        
        if platform_slug:
            platform_query = query + " AND platform_slug = %s LIMIT 1"
            platform_params = params + [platform_slug]
            cursor.execute(platform_query, tuple(platform_params))
            res = cursor.fetchone()
            if res: return res['romm_id']
        
        # Fallback: search across all platforms if platform-specific failed or wasn't provided
        # (Handles cases like GBA games being played on a GameCube emulator)
        fallback_query = query + " LIMIT 1"
        cursor.execute(fallback_query, tuple(params))
        res = cursor.fetchone()
        if res: return res['romm_id']
        
    return None

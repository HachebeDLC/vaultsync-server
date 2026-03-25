import logging
import asyncio
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from psycopg2.extras import RealDictCursor
from cachetools import TTLCache
from .config import SECRET_KEY, ALGORITHM
from .database import get_db

logger = logging.getLogger("VaultSync")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# Priority 3: Cache user records to reduce DB pressure
user_cache = TTLCache(maxsize=100, ttl=300)
_user_cache_lock = asyncio.Lock()

async def get_current_user(token: str = Depends(oauth2_scheme)):
    """Validates the JWT token and returns the current user from the database."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token: missing subject")

        # Check cache first
        async with _user_cache_lock:
            if user_id in user_cache:
                return user_cache[user_id]

        # Use asyncio.to_thread for synchronous DB driver to avoid blocking the event loop
        def fetch_user():
            with get_db() as conn:
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
                return cursor.fetchone()

        user = await asyncio.to_thread(fetch_user)

        if user is None:
            raise HTTPException(status_code=401, detail="User not found")

        async with _user_cache_lock:
            user_cache[user_id] = user
        return user
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Auth DB error: {e}")
        raise HTTPException(status_code=503, detail="Authentication service unavailable")

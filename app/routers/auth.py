import logging
import traceback
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from jose import jwt
from passlib.context import CryptContext
from psycopg2 import errors as pg_errors

from ..config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES, REFRESH_TOKEN_EXPIRE_DAYS
from ..database import get_db
from ..models import UserLogin, UserRegister, TokenRefreshRequest
from ..dependencies import get_current_user
from ..limiter import limiter
from .. import crud
import os
import time
import secrets

logger = logging.getLogger("VaultSync")
router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def create_tokens(user_id: int):
    access_token = jwt.encode(
        {"sub": str(user_id), "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)}, 
        SECRET_KEY, 
        algorithm=ALGORITHM
    )
    refresh_token = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + (REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60)
    
    with get_db() as conn:
        crud.create_refresh_token(conn, user_id, refresh_token, expires_at)
        conn.commit()
        
    return access_token, refresh_token

@router.post("/register")
@limiter.limit("5/minute")
def register(request: Request, user: UserRegister):
    """
    Registers a new user and returns access and refresh tokens.
    Zero-knowledge salts are generated on the server but encryption happens on the client.
    """
    logger.info(f"📝 REGISTER: Attempt for email {user.email}")
    
    try:
        hashed_password = pwd_context.hash(user.password)
        salt = os.urandom(16).hex()
        
        with get_db() as conn:
            user_id = crud.create_user(conn, user.email, hashed_password, user.username, salt)
            conn.commit()
            logger.info(f"✅ REGISTER: Created user {user_id}")
            
        access_token, refresh_token = create_tokens(user_id)
        return {
            "token": access_token, 
            "refresh_token": refresh_token,
            "user": {"id": str(user_id), "email": user.email, "salt": salt}
        }
        
    except pg_errors.UniqueViolation:
        logger.warning(f"⚠️ REGISTER: Email already exists: {user.email}")
        raise HTTPException(status_code=400, detail="User already exists")
        
    except Exception as e:
        logger.error(f"❌ REGISTER ERROR: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Registration failed due to a server error")

@router.post("/login")
@limiter.limit("5/minute")
def login(request: Request, credentials: UserLogin):
    """
    Authenticates a user and returns access and refresh tokens.
    """
    logger.info(f"🔑 LOGIN: Attempt for email {credentials.email}")
    try:
        with get_db() as conn:
            user = crud.get_user_by_email(conn, credentials.email)
            
        if not user or not pwd_context.verify(credentials.password, user['password_hash']):
            logger.warning(f"⚠️ LOGIN: Invalid credentials for {credentials.email}")
            raise HTTPException(status_code=401, detail="Invalid credentials")
            
        access_token, refresh_token = create_tokens(user['id'])
        logger.info(f"✅ LOGIN: Success for user {user['id']}")
        return {
            "token": access_token, 
            "refresh_token": refresh_token,
            "user": {
                "id": str(user['id']), 
                "email": user['email'], 
                "salt": user.get('salt') or user['email']
            }
        }
    except Exception as e:
        logger.error(f"❌ LOGIN ERROR: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Authentication failed")

@router.post("/refresh")
def refresh(payload: TokenRefreshRequest):
    """
    Exchanges a valid refresh token for a new access token.
    """
    try:
        with get_db() as conn:
            db_token = crud.get_refresh_token(conn, payload.refresh_token)
            
            if not db_token:
                raise HTTPException(status_code=401, detail="Invalid or revoked refresh token")
                
            if db_token['expires_at'] < int(time.time()):
                crud.revoke_refresh_token(conn, payload.refresh_token)
                conn.commit()
                raise HTTPException(status_code=401, detail="Refresh token expired")
                
            # Issue new access token, keep same refresh token for now or rotation
            # For simplicity, we just issue a new access token
            access_token = jwt.encode(
                {"sub": str(db_token['user_id']), "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)}, 
                SECRET_KEY, 
                algorithm=ALGORITHM
            )
            
            return {"token": access_token}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ REFRESH ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail="Refresh failed")

@router.post("/logout")
def logout(payload: TokenRefreshRequest, current_user = Depends(get_current_user)):
    """
    Revokes a refresh token.
    """
    with get_db() as conn:
        crud.revoke_refresh_token(conn, payload.refresh_token)
        conn.commit()
    return {"status": "ok"}


@router.get("/auth/me")
def auth_me(current_user = Depends(get_current_user)):
    """
    Returns basic information about the currently authenticated user.
    """
    return {"id": str(current_user['id']), "email": current_user['email']}

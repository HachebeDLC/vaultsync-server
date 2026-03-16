import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from jose import jwt
from passlib.context import CryptContext
from psycopg2 import errors as pg_errors

from ..config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES
from ..database import get_db
from ..models import UserLogin, UserRegister
from ..dependencies import get_current_user
from ..limiter import limiter
from .. import crud
import os

logger = logging.getLogger("VaultSync")
router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

@router.post("/register")
@limiter.limit("5/minute")
def register(request: Request, user: UserRegister):
    """
    Registers a new user and returns an access token.
    Zero-knowledge salts are generated on the server but encryption happens on the client.
    """
    hashed_password = pwd_context.hash(user.password)
    salt = os.urandom(16).hex()
    try:
        with get_db() as conn:
            user_id = crud.create_user(conn, user.email, hashed_password, user.username, salt)
            conn.commit()
            
        token = jwt.encode(
            {"sub": str(user_id), "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)}, 
            SECRET_KEY, 
            algorithm=ALGORITHM
        )
        return {"token": token, "user": {"id": str(user_id), "email": user.email, "salt": salt}}
    except pg_errors.UniqueViolation:
        logger.warning(f"Registration attempt for existing email: {user.email}")
        raise HTTPException(status_code=400, detail="User already exists")
    except Exception as e:
        logger.error(f"Unexpected registration error: {e}")
        raise HTTPException(status_code=500, detail="Registration failed due to a server error")

@router.post("/login")
@limiter.limit("5/minute")
def login(request: Request, credentials: UserLogin):
    """
    Authenticates a user and returns a JWT access token.
    """
    with get_db() as conn:
        user = crud.get_user_by_email(conn, credentials.email)
        
    if not user or not pwd_context.verify(credentials.password, user['password_hash']):
        raise HTTPException(status_code=401, detail="Invalid credentials")
        
    token = jwt.encode(
        {"sub": str(user['id']), "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)}, 
        SECRET_KEY, 
        algorithm=ALGORITHM
    )
    return {
        "token": token, 
        "user": {
            "id": str(user['id']), 
            "email": user['email'], 
            "salt": user.get('salt') or user['email']
        }
    }

@router.get("/auth/me")
def auth_me(current_user = Depends(get_current_user)):
    """
    Returns basic information about the currently authenticated user.
    """
    return {"id": str(current_user['id']), "email": current_user['email']}

from fastapi import APIRouter, Depends, HTTPException, Request
from ..database import get_db
from ..models import RecoverySetupRequest, RecoveryPayloadRequest
from ..dependencies import get_current_user
from ..limiter import limiter
from .. import crud

router = APIRouter(prefix="/api/v1/auth/recovery")

@router.post("/setup")
def setup_recovery(body: RecoverySetupRequest, current_user = Depends(get_current_user)):
    """
    Saves the user's encrypted recovery payload and salt for future fail-safe recovery.
    """
    with get_db() as conn:
        crud.update_user_recovery(conn, current_user['id'], body.recovery_payload, body.recovery_salt)
        conn.commit()
    return {"message": "OK"}

@router.post("/payload")
@limiter.limit("5/minute")
def get_recovery_payload(request: Request, body: RecoveryPayloadRequest):
    """
    Retrieves the recovery payload for a given email address. 
    Requires email as a key but doesn't require authentication (used when password is lost).
    Rate limited to prevent email harvesting.
    """
    with get_db() as conn:
        user = crud.get_recovery_info(conn, body.email)
        
    if not user or not user['recovery_payload']:
        raise HTTPException(status_code=404, detail="Recovery information not found")
    return user

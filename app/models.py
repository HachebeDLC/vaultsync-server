from typing import List, Optional
from pydantic import BaseModel

class UserLogin(BaseModel):
    email: str
    password: str

class UserRegister(BaseModel):
    email: str
    password: str
    username: Optional[str] = None

class FileRequest(BaseModel):
    filename: str

class RestoreRequest(BaseModel):
    path: str
    version_id: str

class BlockCheckRequest(BaseModel):
    path: str
    blocks: List[str]

class BlockDownloadRequest(BaseModel):
    path: str
    indices: List[int]

class FinalizeRequest(BaseModel):
    path: str
    hash: str
    size: Optional[int] = None
    updated_at: int
    device_name: str = "Unknown"

class RecoverySetupRequest(BaseModel):
    recovery_payload: str
    recovery_salt: str

class RecoveryPayloadRequest(BaseModel):
    email: str

class TokenRefreshRequest(BaseModel):
    refresh_token: str

class RomMSyncRequest(BaseModel):
    path: str
    key: str # User's ZK key (base64 or hex)

class RomMPullRequest(BaseModel):
    rom_id: int
    # `target_path` is accepted for backward compatibility but unused server-side:
    # the pull endpoint streams plaintext bytes back to the client so the client
    # can encrypt locally and upload via the normal /upload flow. The client picks
    # the destination path itself.
    target_path: Optional[str] = None

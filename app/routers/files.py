import os
import json
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from ..config import STORAGE_DIR, ENCRYPTED_BLOCK_SIZE
from ..database import get_db
from ..models import FileRequest, RestoreRequest, BlockCheckRequest, BlockDownloadRequest, FinalizeRequest
from ..dependencies import get_current_user
from ..utils import is_safe_path, calculate_file_hash_and_blocks
from ..services.version_manager import version_manager
from .. import crud

logger = logging.getLogger("VaultSync")
router = APIRouter(prefix="/api/v1")

@router.get("/files")
def list_files(prefix: Optional[str] = None, current_user = Depends(get_current_user)):
    """
    Returns a list of all files synced by the current user, optionally filtered by path prefix.
    """
    with get_db() as conn:
        return {"files": crud.list_user_files(conn, current_user['id'], prefix=prefix)}

@router.get("/versions")
def list_versions(path: str, current_user = Depends(get_current_user)):
    """
    Lists all available historical versions for a specific file path.
    """
    if not is_safe_path(current_user['id'], path):
        raise HTTPException(status_code=403, detail="Forbidden: Path traversal detected")
    versions = version_manager.list_versions(current_user['id'], path)
    return {"path": path, "versions": versions}

@router.post("/versions/restore")
def restore_version(body: RestoreRequest, current_user = Depends(get_current_user)):
    """
    Restores a specific version of a file. The current version is backed up before restoration.
    """
    user_id = current_user['id']
    if not is_safe_path(user_id, body.path):
        raise HTTPException(status_code=403, detail="Forbidden")
    
    try:
        # Create a version of the current file before restoring
        with get_db() as conn:
            metadata = crud.get_file_metadata(conn, user_id, body.path)
            if metadata:
                version_manager.create_version(user_id, body.path, metadata['device_name'])
        
        version_manager.restore_version(user_id, body.path, body.version_id)
        
        # Update metadata to match the restored file
        user_root = os.path.join(STORAGE_DIR, str(user_id))
        safe_path = os.path.abspath(os.path.join(user_root, body.path.lstrip("/\\")))
        
        actual_hash, block_hashes = calculate_file_hash_and_blocks(safe_path)
        
        with get_db() as conn:
            crud.update_file_sync(
                conn, user_id, body.path, actual_hash, 
                os.path.getsize(safe_path), int(os.path.getmtime(safe_path) * 1000), 
                json.dumps(block_hashes)
            )
            conn.commit()
            
        return {"message": "Success", "hash": actual_hash}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Version not found")
    except Exception as e:
        logger.error(f"❌ Restore failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/files/manifest")
def get_file_manifest(path: str, current_user = Depends(get_current_user)):
    """
    Returns the block manifest (list of SHA-256 hashes) for a specific file.
    """
    if not is_safe_path(current_user['id'], path):
        raise HTTPException(status_code=403)
    with get_db() as conn:
        metadata = crud.get_file_metadata(conn, current_user['id'], path)
    if not metadata:
        raise HTTPException(status_code=404)
    return {"path": path, "blocks": json.loads(metadata['blocks']) if metadata['blocks'] else []}

@router.post("/blocks/check")
def check_blocks(body: BlockCheckRequest, current_user = Depends(get_current_user)):
    """
    Compares local block hashes with server block hashes and returns indices of missing/modified blocks.
    """
    if not is_safe_path(current_user['id'], body.path):
        raise HTTPException(status_code=403)
    with get_db() as conn:
        metadata = crud.get_file_metadata(conn, current_user['id'], body.path)
    server_blocks = json.loads(metadata['blocks']) if metadata and metadata['blocks'] else []
    return {"missing": [i for i, h in enumerate(body.blocks) if i >= len(server_blocks) or server_blocks[i] != h]}

@router.post("/blocks/download")
def download_blocks(body: BlockDownloadRequest, current_user = Depends(get_current_user)):
    """
    Streams specific blocks of a file based on provided indices.
    """
    if not is_safe_path(current_user['id'], body.path):
        raise HTTPException(status_code=403)
    safe_path = os.path.abspath(os.path.join(STORAGE_DIR, str(current_user['id']), body.path.lstrip("/\\")))
    if not os.path.exists(safe_path):
        raise HTTPException(status_code=404)

    def iter_blocks():
        with open(safe_path, "rb") as f:
            for index in body.indices:
                offset = index * ENCRYPTED_BLOCK_SIZE
                f.seek(offset)
                chunk = f.read(ENCRYPTED_BLOCK_SIZE)
                if chunk:
                    yield chunk
    return StreamingResponse(iter_blocks(), media_type="application/octet-stream")

@router.post("/upload")
async def upload_fragment(request: Request, current_user = Depends(get_current_user)):
    """
    Uploads a file fragment at a specific offset. Used for both full uploads and delta patching.
    Requires 'x-vaultsync-path' and 'x-vaultsync-offset' headers.
    """
    headers = request.headers
    path = headers.get("x-vaultsync-path")
    offset = int(headers.get("x-vaultsync-offset") or 0)
    if not path or not is_safe_path(current_user['id'], path):
        raise HTTPException(status_code=403)
    
    user_id = current_user['id']
    safe_path = os.path.abspath(os.path.join(STORAGE_DIR, str(user_id), path.lstrip("/\\")))
    os.makedirs(os.path.dirname(safe_path), exist_ok=True)
    
    # Version before overwrite at offset 0
    if offset == 0 and os.path.exists(safe_path):
        with get_db() as conn:
            metadata = crud.get_file_metadata(conn, user_id, path)
            if metadata:
                version_manager.create_version(user_id, path, metadata['device_name'])
                
    with open(safe_path, "r+b" if os.path.exists(safe_path) else "wb") as f:
        f.seek(offset)
        async for chunk in request.stream():
            f.write(chunk)
    return {"message": "OK"}

@router.post("/upload/finalize")
def finalize_upload(body: FinalizeRequest, current_user = Depends(get_current_user)):
    """
    Finalizes a file upload, calculating its final hash and updating the database manifest.
    """
    user_id = current_user['id']
    if not is_safe_path(user_id, body.path):
        raise HTTPException(status_code=403)
        
    safe_path = os.path.abspath(os.path.join(STORAGE_DIR, str(user_id), body.path.lstrip("/\\")))
    if not os.path.exists(safe_path):
        raise HTTPException(status_code=404)
        
    actual_hash, block_hashes = calculate_file_hash_and_blocks(safe_path)
    
    with get_db() as conn:
        crud.upsert_file_metadata(
            conn, user_id, body.path, actual_hash, 
            body.size or os.path.getsize(safe_path), body.updated_at, 
            body.device_name, json.dumps(block_hashes)
        )
        conn.commit()
    return {"message": "Success", "hash": actual_hash}

@router.post("/download")
def download_file(body: FileRequest, current_user = Depends(get_current_user)):
    """
    Downloads a full file from the server.
    """
    if not is_safe_path(current_user['id'], body.filename):
        raise HTTPException(status_code=403)
    safe_path = os.path.abspath(os.path.join(STORAGE_DIR, str(current_user['id']), body.filename.lstrip("/\\")))
    if not os.path.exists(safe_path):
        raise HTTPException(status_code=404)
    return FileResponse(safe_path, media_type="application/octet-stream")

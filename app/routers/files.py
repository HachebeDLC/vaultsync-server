import os
import logging
import asyncio
import aiofiles
import hashlib
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response, BackgroundTasks, Header
from fastapi.responses import FileResponse, StreamingResponse

from ..config import STORAGE_DIR, get_block_size, OVERHEAD, get_encrypted_block_size
from ..services.event_notifier import event_notifier
from ..database import get_db
from ..models import FileRequest, RestoreRequest, BlockCheckRequest, BlockDownloadRequest, FinalizeRequest, RomMSyncRequest
from ..dependencies import get_current_user
import asyncio
# Global lock for RomM API rate limiting
romm_push_lock = asyncio.Lock()

from ..services.reassembly_service import reassembly_service
from ..services.romm_client import romm_client, RomMClient
from ..utils import is_safe_path, calculate_file_hash_and_blocks
from ..services.version_manager import version_manager
from .. import crud

logger = logging.getLogger("VaultSync")
router = APIRouter(prefix="/api/v1")

# Per-file upload locks keyed by (user_id, path).
# Prevents two concurrent finalize_upload calls for the same file from
# racing on truncate → upsert → complete_upload.
_upload_locks: dict[tuple, asyncio.Lock] = {}
_upload_locks_mutex = asyncio.Lock()

async def _get_upload_lock(user_id: int, path: str) -> asyncio.Lock:
    key = (user_id, path)
    async with _upload_locks_mutex:
        if key not in _upload_locks:
            _upload_locks[key] = asyncio.Lock()
        return _upload_locks[key]

@router.get("/files")
def list_files(prefix: Optional[str] = None, limit: int = 200, after: Optional[str] = None, current_user = Depends(get_current_user)):
    """
    Returns a paginated list of files synced by the current user, optionally filtered by path prefix.
    Pass the returned next_cursor as the 'after' param to fetch the next page.
    """
    limit = min(limit, 1000)
    with get_db() as conn:
        files, next_cursor = crud.list_user_files(conn, current_user['id'], prefix=prefix, limit=limit, after=after)
        return {"files": files, "next_cursor": next_cursor}

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
        return {"path": path, "blocks": metadata.get('blocks', [])}

@router.post("/blocks/check")
async def check_blocks(body: BlockCheckRequest, current_user = Depends(get_current_user)):
    """
    Given a list of block hashes, returns the indices that are missing or different on the server.
    """
    user_id = current_user['id']

    def _get_metadata():
        with get_db() as conn:
            return crud.get_file_metadata(conn, user_id, body.path)

    metadata = await asyncio.to_thread(_get_metadata)
    if not metadata:
        return {"missing": list(range(len(body.blocks)))}

    server_blocks = metadata.get('blocks', [])
    missing = [i for i, h in enumerate(body.blocks) if i >= len(server_blocks) or server_blocks[i] != h]
    return {"missing": missing}

@router.post("/blocks/download")
async def download_blocks(body: BlockDownloadRequest, current_user = Depends(get_current_user)):
    """
    Downloads specific encrypted blocks for a file.
    Used for resuming or delta-downloading.
    """
    if not is_safe_path(current_user['id'], body.path):
        raise HTTPException(status_code=403)
        
    safe_path = os.path.abspath(os.path.join(STORAGE_DIR, str(current_user['id']), body.path.lstrip("/\\")))
    if not os.path.exists(safe_path):
        raise HTTPException(status_code=404)

    def _get_file_size():
        with get_db() as conn:
            m = crud.get_file_metadata(conn, current_user['id'], body.path)
            return m['size'] if m else os.path.getsize(safe_path)

    file_size = await asyncio.to_thread(_get_file_size)
    enc_block_size = get_encrypted_block_size(file_size)

    async def iter_blocks():
        if not body.indices:
            return

        # Group contiguous indices
        sorted_indices = sorted(body.indices)
        groups = []
        if not sorted_indices: return
        
        current_group = [sorted_indices[0]]

        for i in range(1, len(sorted_indices)):
            if sorted_indices[i] == current_group[-1] + 1:
                current_group.append(sorted_indices[i])
            else:
                groups.append(current_group)
                current_group = [sorted_indices[i]]
        if current_group:
            groups.append(current_group)

        async with aiofiles.open(safe_path, "rb") as f:
            for group in groups:
                start_offset = group[0] * enc_block_size
                read_size = len(group) * enc_block_size
                
                await f.seek(start_offset)
                
                # Stream the merged group in chunks to avoid blowing up memory if the group is huge
                bytes_left = read_size
                chunk_size = 1024 * 1024 * 4  # 4MB max per yield
                while bytes_left > 0:
                    chunk = await f.read(min(chunk_size, bytes_left))
                    if not chunk:
                        break
                    yield chunk
                    bytes_left -= len(chunk)
                    
    return StreamingResponse(iter_blocks(), media_type="application/octet-stream")

@router.post("/upload")
async def upload_fragment(request: Request, background_tasks: BackgroundTasks, current_user = Depends(get_current_user)):
    """
    Uploads a file fragment at a specific offset. Used for both full uploads and delta patching.
    Requires 'x-vaultsync-path' and 'x-vaultsync-offset' headers.
    """
    headers = request.headers
    import urllib.parse
    raw_path = headers.get("x-vaultsync-path")
    path = urllib.parse.unquote(raw_path) if raw_path else None
    offset = int(headers.get("x-vaultsync-offset") or 0)
    if not path or not is_safe_path(current_user['id'], path):
        raise HTTPException(status_code=403)
        
    user_id = current_user['id']
    safe_path = os.path.abspath(os.path.join(STORAGE_DIR, str(user_id), path.lstrip("/\\")))
    os.makedirs(os.path.dirname(safe_path), exist_ok=True)

    bytes_written = 0
    hasher = hashlib.sha256()

    # Pre-create or open existing file
    if not os.path.exists(safe_path):
        # Using synchronous open for initial creation is fine as it's a one-off
        with open(safe_path, "wb") as f:
            pass
    else:
        # Syncthing-style: snapshot existing file before first fragment overwrites it.
        def _get_meta():
            with get_db() as conn:
                return crud.get_file_metadata(conn, user_id, path)
        existing_meta = await asyncio.to_thread(_get_meta)
        if existing_meta:
            version_manager.begin_upload(user_id, path, existing_meta.get('device_name', 'unknown'))
            
    async with aiofiles.open(safe_path, "r+b") as f:
        await f.seek(offset)
        async for chunk in request.stream():
            await f.write(chunk)
            hasher.update(chunk)
            bytes_written += len(chunk)
            
    return {"message": "Fragment uploaded", "bytes": bytes_written, "sha256": hasher.hexdigest()}

@router.post("/upload/finalize")
async def finalize_upload(request: Request, body: FinalizeRequest, background_tasks: BackgroundTasks, current_user = Depends(get_current_user)):
    """
    Finalizes a file upload. Avoids reading the whole file if smart delta hashing can be used.
    """
    import urllib.parse
    body.path = urllib.parse.unquote(body.path)
    
    user_id = current_user['id']
    if not is_safe_path(user_id, body.path):
        raise HTTPException(status_code=403)
        
    safe_path = os.path.abspath(os.path.join(STORAGE_DIR, str(user_id), body.path.lstrip("/\\")))
    if not os.path.exists(safe_path):
        raise HTTPException(status_code=404)

    actual_hash = body.hash
    block_hashes = []

    file_lock = await _get_upload_lock(user_id, body.path)
    async with file_lock:
        def _get_metadata():
            with get_db() as conn:
                return crud.get_file_metadata(conn, user_id, body.path)

        metadata = await asyncio.to_thread(_get_metadata)
        if metadata and metadata.get('blocks'):
            block_hashes = metadata['blocks']

        # If it's a completely new file or we don't have blocks, calculate the hard way.
        if not block_hashes:
            _, block_hashes = await calculate_file_hash_and_blocks(safe_path)

        size = body.size or os.path.getsize(safe_path)

        # --- CRITICAL FIX: TRUNCATE PHYSICAL FILE ---
        # Prevents "ghost data" if the file has shrunk.
        if size == 0:
            expected_enc_size = 0
        else:
            bs = get_block_size(size)
            num_blocks = (size + bs - 1) // bs
            expected_enc_size = size + (num_blocks * OVERHEAD)

        real_fs_size = os.path.getsize(safe_path)
        if real_fs_size > expected_enc_size:
            logger.info(f"✂️ TRUNCATING: {body.path} from {real_fs_size} to {expected_enc_size}")
            with open(safe_path, "a") as f:
                f.truncate(expected_enc_size)
        # --------------------------------------------

        def _upsert():
            with get_db() as conn:
                crud.upsert_file_metadata(
                    conn, user_id, body.path, actual_hash,
                    size, body.updated_at,
                    body.device_name, block_hashes
                )
                conn.commit()

        await asyncio.to_thread(_upsert)

        # Clear the upload-in-progress marker so the next overwrite of this file
        # will produce a fresh snapshot.
        version_manager.complete_upload(user_id, body.path)

    # Extract system_id directly from the path structure
    system_id = body.path.split('/')[0] if '/' in body.path else 'unknown'

    # Broadcast event to all listening clients
    asyncio.create_task(event_notifier.broadcast_to_user(user_id, {
        "path": body.path,
        "system_id": system_id,
        "size": size,
        "updated_at": body.updated_at,
        "hash": actual_hash,
        "origin_device": body.device_name
    }))
    
    # Optional: Trigger RomM sync if key provided in header
    romm_key = request.headers.get("x-vaultsync-romm-key")
    romm_url_header = request.headers.get("x-romm-url")
    romm_api_key_header = request.headers.get("x-romm-api-key")

    if romm_url_header and romm_api_key_header:
        # Update user's RomM credentials in DB
        def _update_creds():
            with get_db() as conn:
                from psycopg2.extras import RealDictCursor
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                cursor.execute("SELECT romm_url, romm_api_key FROM users WHERE id = %s", (user_id,))
                row = cursor.fetchone()
                if not row or row.get('romm_api_key') != romm_api_key_header:
                    crud.update_user_romm_creds(conn, user_id, romm_url_header, romm_api_key_header)
                    conn.commit()
                    return True
                return False

        creds_changed = await asyncio.to_thread(_update_creds)
        if creds_changed:
            logger.info(f"RomM credentials updated for user {user_id}. Queueing full library sync.")
            
            async def _pull_library():
                try:
                    from ..services.romm_client import RomMClient
                    client = RomMClient(romm_url_header, romm_api_key_header)
                    all_games = await client.fetch_entire_library()
                    logger.info(f"RomM Sync: Downloaded {len(all_games)} games for user {user_id}.")
                    
                    def _save_lib():
                        with get_db() as conn:
                            crud.sync_user_romm_library(conn, user_id, all_games)
                            conn.commit()
                            
                    await asyncio.to_thread(_save_lib)
                    logger.info(f"RomM Sync: Successfully populated local library cache for user {user_id}.")
                except Exception as e:
                    logger.error(f"RomM Sync: Failed to pull library for user {user_id}: {str(e)}")
            
            background_tasks.add_task(_pull_library)

    if romm_key:
        logger.info(f"Triggering automatic RomM sync for {body.path}")
        try:
            await romm_sync(RomMSyncRequest(path=body.path, key=romm_key), background_tasks, current_user)
        except Exception as e:
            logger.error(f"Automatic RomM sync failed for {body.path}: {e}")

    return {"message": "Finalized"}




@router.post("/romm/sync")
async def romm_sync(body: RomMSyncRequest, background_tasks: BackgroundTasks, current_user = Depends(get_current_user)):
    """
    Manually triggers a reassembly and RomM push for a file.
    """
    import urllib.parse
    body.path = urllib.parse.unquote(body.path)
    
    user_id = current_user['id']
    if not is_safe_path(user_id, body.path):
        raise HTTPException(status_code=403)
        
    safe_path = os.path.abspath(os.path.join(STORAGE_DIR, str(user_id), body.path.lstrip("/\\")))
    if not os.path.exists(safe_path):
        raise HTTPException(status_code=404)

    def _get_metadata():
        with get_db() as conn:
            return crud.get_file_metadata(conn, user_id, body.path)
            
    metadata = await asyncio.to_thread(_get_metadata)
    if not metadata:
        raise HTTPException(status_code=404)

    # Decode key (assumed base64 from client)
    # Decode key (assumed base64Url from client)
    import base64
    try:
        # Handle missing padding
        key_b64 = body.key
        missing_padding = len(key_b64) % 4
        if missing_padding:
            key_b64 += '=' * (4 - missing_padding)
        raw_key = base64.urlsafe_b64decode(key_b64)
    except Exception as e:
        logger.error(f"Base64 decode failed: {str(e)}")
        raise HTTPException(status_code=400, detail="Invalid key format (base64Url expected)")
        
    raw_key = raw_key[:32]
    if len(raw_key) != 32:
        raise HTTPException(status_code=400, detail="Invalid key length (32 bytes expected for AES-256)")

    # Resolve RomM Client (use user specific if available)
    target_client = romm_client
    if current_user.get('romm_url') and current_user.get('romm_api_key'):
        target_client = RomMClient(current_user['romm_url'], current_user['romm_api_key'])
        logger.info(f"Using user-specific RomM instance: {current_user['romm_url']}")

    # Run reassembly in background to avoid blocking
    temp_reassembly_dir = os.path.join(STORAGE_DIR, "temp_reassembly", str(user_id))
    os.makedirs(temp_reassembly_dir, exist_ok=True)
    
    out_name = os.path.basename(body.path)
    output_path = os.path.join(temp_reassembly_dir, out_name)
    zip_path = output_path + ".zip"

    async def _do_sync():
        try:
            # 1. Reassemble locally (can be parallel)
            await asyncio.to_thread(
                reassembly_service.reassemble_file, 
                safe_path, output_path, raw_key, metadata['size']
            )
            
            # 2. Sequential execution block for RomM operations
            async with romm_push_lock:
                # A. Local RomM Match
                from ..services.title_db_service import title_db
                import re
                import os
                from ..database import get_db
                from .. import crud
                
                parts = body.path.split("/")
                platform = parts[0].lower()
                
                platform_map = {
                    'switch': 'switch', 'eden': 'switch',
                    'gc': 'gamecube', 'dolphin': 'gamecube', 'wii': 'wii',
                    'psp': 'psp', 'ppsspp': 'psp',
                    'ps2': 'ps2', 'pcsx2': 'ps2', 'aethersx2': 'ps2',
                    '3ds': '3ds', 'citra': '3ds', 'azahar': '3ds',
                    'gba': 'gba', 'snes': 'snes', 'n64': 'n64', 'nds': 'nds',
                    'gb': 'gb', 'gbc': 'gbc', 'nes': 'nes', 'megadrive': 'megadrive'
                }
                
                romm_platform = platform_map.get(platform)
                target_id = None
                target_name = parts[-1]
                
                if platform in ('switch', 'eden') and len(parts) >= 3:
                    target_id = parts[1].upper()
                elif platform in ('gc', 'dolphin', 'wii') and len(parts) >= 2:
                    target_id = parts[1].split('.')[0].upper()
                elif platform in ('psp', 'ppsspp') and len(parts) >= 3 and parts[1] == 'SAVEDATA':
                    target_id = parts[2].upper()
                elif platform == '3ds' and len(parts) >= 3 and parts[1] == 'saves':
                    target_id = parts[2].upper()

                translated_name = None
                if target_id:
                    translated_name = title_db.translate(target_id)

                if translated_name:
                    target_name = translated_name

                clean_target = os.path.splitext(target_name)[0]
                clean_target = re.sub(r'^\d+\.\s*', '', clean_target).lower().strip()

                def _local_match():
                    with get_db() as conn:
                        found_id = crud.find_romm_game_for_user(conn, user_id, target_id, clean_target, romm_platform)
                        if not found_id:
                            cursor = conn.cursor()
                            cursor.execute("SELECT COUNT(*) FROM romm_games WHERE user_id = %s", (user_id,))
                            count = cursor.fetchone()[0]
                            if count == 0:
                                return "NEEDS_SYNC"
                        return found_id
                
                rom_id = await asyncio.to_thread(_local_match)
                
                if rom_id == "NEEDS_SYNC":
                    logger.info(f"RomM Cache is empty for user {user_id}. Fetching full library...")
                    all_games = await target_client.fetch_entire_library()
                    logger.info(f"RomM Sync: Downloaded {len(all_games)} games.")
                    def _save_lib():
                        with get_db() as conn:
                            crud.sync_user_romm_library(conn, user_id, all_games)
                            conn.commit()
                            return crud.find_romm_game_for_user(conn, user_id, target_id, clean_target, romm_platform)
                    rom_id = await asyncio.to_thread(_save_lib)
                    
                if not rom_id:
                    logger.warning(f"Could not find RomM match for {body.path} in local cache. Skipping.")
                    return

                # B. Push raw file to RomM
                success = await target_client.upload_save(rom_id, output_path, device_id=f"NeoSync-{metadata.get('device_name', 'Unknown')}")
                if success:
                    logger.info(f"Successfully synced {body.path} to RomM ID {rom_id}")
                
                # C. Mandatory cool-down before releasing lock to next task
                await asyncio.sleep(1.5)
            
        except Exception as e:
            logger.error(f"RomM Sync failed for {body.path}: {str(e)}")
        finally:
            if os.path.exists(output_path): os.remove(output_path)

    background_tasks.add_task(_do_sync)
    return {"message": "RomM sync task queued"}

@router.delete("/files")
@router.delete("/files")
async def delete_file(body: FileRequest, background_tasks: BackgroundTasks, current_user = Depends(get_current_user)):
    """
    Deletes a file and its metadata. A historical version is created before deletion.
    """
    path = body.filename
    if not is_safe_path(current_user['id'], path):
        raise HTTPException(status_code=403)
        
    user_id = current_user['id']
    
    def _delete_metadata():
        with get_db() as conn:
            metadata = crud.get_file_metadata(conn, user_id, path)
            if metadata:
                background_tasks.add_task(version_manager.create_version, user_id, path, metadata['device_name'])
            crud.delete_file_metadata(conn, user_id, path)
            conn.commit()

    await asyncio.to_thread(_delete_metadata)
        
    safe_path = os.path.abspath(os.path.join(STORAGE_DIR, str(user_id), path.lstrip("/\\")))
    if os.path.exists(safe_path):
        os.remove(safe_path)
        
    return {"message": "Deleted"}

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
async def restore_version(body: RestoreRequest, current_user = Depends(get_current_user)):
    """
    Restores a specific version of a file.
    """
    user_id = current_user['id']
    if not is_safe_path(user_id, body.path):
        raise HTTPException(status_code=403)
    
    success = await version_manager.restore_version(user_id, body.path, body.version_id)
    if not success:
        raise HTTPException(status_code=500, detail="Restore failed")
        
    def _get_meta():
        with get_db() as conn:
            return crud.get_file_metadata(conn, user_id, body.path)
    
    meta = await asyncio.to_thread(_get_meta)
    if meta:
        system_id = body.path.split('/')[0] if '/' in body.path else 'unknown'
        asyncio.create_task(event_notifier.broadcast_to_user(user_id, {
            "path": body.path,
            "system_id": system_id,
            "size": meta['size'],
            "updated_at": meta['updated_at'],
            "hash": meta['hash'],
            "origin_device": "VersionRestore"
        }))
        
    return {"message": "Restored"}

@router.get("/conflicts")
def list_conflicts(current_user = Depends(get_current_user)):
    """
    Lists all active sync conflicts for the user.
    """
    # Stub: Conflicts are currently handled via the .sync-conflict- suffix 
    # in the file list. This endpoint can be used for a centralized UI later.
    return {"conflicts": []}

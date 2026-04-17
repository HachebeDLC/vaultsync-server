import asyncio
import logging
from datetime import datetime, timezone
import os

from ..database import get_db
from ..crud import get_files_with_romm_id, upsert_file_metadata, get_file_metadata
from .romm_client import RomMClient
from ..utils import calculate_file_hash_and_blocks
from ..config import STORAGE_DIR

logger = logging.getLogger("VaultSync")

async def _process_user_romm_sync(user_id: int):
    # Retrieve user's romm creds
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT romm_url, romm_api_key FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        
    if not row or not row[0] or not row[1]:
        return
        
    client = RomMClient(row[0], row[1])
    
    # Get all files mapping to a romm_id for this user
    with get_db() as conn:
        mapped_files = get_files_with_romm_id(conn, user_id)
        
    if not mapped_files:
        return
        
    logger.info(f"Auto-Sync: Checking {len(mapped_files)} RomM-linked files for user {user_id}")
    
    for f in mapped_files:
        path = f['path']
        romm_id = f['romm_id']
        local_updated_at = f['updated_at'] # epoch ms
        
        try:
            import httpx
            async with httpx.AsyncClient() as http:
                resp = await http.get(
                    f"{client.base_url}/api/saves",
                    params={"rom_id": romm_id},
                    headers=client.headers,
                    timeout=30.0
                )
                
            if resp.status_code != 200:
                continue
                
            saves = resp.json()
            if not saves:
                continue
                
            latest_save = sorted(saves, key=lambda x: x.get('updated_at', ''), reverse=True)[0]
            
            # Parse ISO 8601 string to ms
            try:
                # E.g. 2026-04-17T12:00:00Z
                dt_str = latest_save.get('updated_at', '')
                if not dt_str: continue
                # Replace Z with +00:00 for fromisoformat
                dt_str = dt_str.replace('Z', '+00:00')
                dt = datetime.fromisoformat(dt_str)
                romm_updated_at = int(dt.timestamp() * 1000)
            except Exception:
                continue
                
            # If RomM has a newer save (allow a 5-second delta to avoid push/pull loops)
            if romm_updated_at > local_updated_at + 5000:
                logger.info(f"Auto-Sync: Found newer save on RomM for {path} (RomM: {romm_updated_at} > Local: {local_updated_at})")
                
                # We do a pull!
                temp_pull_dir = os.path.join(STORAGE_DIR, "temp_pull", str(user_id))
                safe_path = os.path.abspath(os.path.join(STORAGE_DIR, str(user_id), path.lstrip("/\\")))
                
                downloaded_path = await client.download_save(romm_id, temp_pull_dir)
                if downloaded_path:
                    os.makedirs(os.path.dirname(safe_path), exist_ok=True)
                    import shutil
                    shutil.move(downloaded_path, safe_path)
                    
                    file_size = os.path.getsize(safe_path)
                    file_hash, blocks = await calculate_file_hash_and_blocks(safe_path)
                    
                    with get_db() as conn:
                        upsert_file_metadata(
                            conn, user_id, path, file_hash, file_size,
                            romm_updated_at, "RomM-AutoSync", blocks
                        )
                        conn.commit()
                    logger.info(f"Auto-Sync: Successfully updated {path} from RomM")
        except Exception as e:
            logger.error(f"Auto-Sync error on {path}: {e}")
            
async def auto_sync_loop():
    logger.info("RomM Auto-Sync background task started.")
    while True:
        try:
            # Sleep for an hour or so, but initially wait a minute so server fully starts
            await asyncio.sleep(60)
            
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM users WHERE romm_api_key IS NOT NULL AND romm_url IS NOT NULL")
                users = cursor.fetchall()
                
            for u in users:
                await _process_user_romm_sync(u[0])
                
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Auto-Sync loop encountered an error: {e}")
        
        # Check every 10 minutes
        await asyncio.sleep(600)

import asyncio
import logging
from datetime import datetime, timezone

from ..database import get_db
from ..crud import get_files_with_romm_id
from .romm_client import get_romm_client
from .event_notifier import event_notifier

logger = logging.getLogger("VaultSync")

async def _process_user_romm_sync(user_id: int):
    # Retrieve user's romm creds
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT romm_url, romm_api_key FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        
    if not row or not row[0] or not row[1]:
        return
        
    client = get_romm_client(row[0], row[1])
    
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

                # Zero-knowledge: the server must never write to the vault. Just
                # notify the client so it can pull, encrypt locally, and upload
                # through the normal flow. No dedupe here — while the condition
                # holds we re-notify every cycle (a retry for offline clients);
                # it stops once the client uploads and updated_at catches up.
                system_id = path.split('/')[0] if '/' in path else 'unknown'
                await event_notifier.broadcast_to_user(user_id, {
                    "type": "romm_save_newer",
                    "path": path,
                    "system_id": system_id,
                    "romm_id": romm_id,
                    "romm_updated_at": romm_updated_at,
                }, event="romm_save_newer")
                logger.info(f"Auto-Sync: Notified user {user_id} of newer RomM save for {path}")
        except Exception as e:
            logger.error(f"Auto-Sync error on {path}: {e}")
            
async def auto_sync_loop():
    logger.info("RomM Auto-Sync background task started.")
    # Wait for the server to finish starting before the first cycle.
    await asyncio.sleep(60)
    while True:
        try:
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

        # Check every 10 minutes.
        await asyncio.sleep(600)

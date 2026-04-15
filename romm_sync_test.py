import os
import sys
import asyncio
import argparse
import base64
import tempfile
import logging

# Manual .env loader
def load_env(env_path):
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    key, value = line.strip().split('=', 1)
                    os.environ[key] = value.strip('"').strip("'")

# Try to load from vaultsync_server/.env or current dir
load_env(os.path.join(os.getcwd(), "vaultsync_server", ".env"))
load_env(".env")

# Add vaultsync_server to path
sys.path.append(os.path.join(os.getcwd(), "vaultsync_server"))

from app.database import get_db, init_db
from app.services.reassembly_service import reassembly_service
from app.services.romm_client import RomMClient
from app.services.title_db_service import title_db
from app.config import STORAGE_DIR
from app import crud

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("RomMSyncTest")

async def match_saves(user_email, zk_key_b64, dry_run=True, override_romm_url=None, override_romm_key=None):
    # 1. Initialize DB and connect
    try:
        with get_db() as conn:
            # 2. Get user
            user = crud.get_user_by_email(conn, user_email)
            if not user:
                logger.error(f"User {user_email} not found")
                return

            user_id = user['id']
            
            # Logic: CLI override > DB value
            romm_url = override_romm_url or user.get('romm_url')
            romm_api_key = override_romm_key or user.get('romm_api_key')

            if not dry_run and (not romm_url or not romm_api_key):
                logger.error("RomM credentials missing (neither in DB nor provided via CLI). Cannot push.")
                return

            # Decode ZK key
            try:
                zk_key = base64.b64decode(zk_key_b64)
            except Exception as e:
                logger.error(f"Failed to decode ZK key: {e}")
                return

            # Initialize RomM client for this user if credentials exist
            client = RomMClient(base_url=romm_url, api_key=romm_api_key) if romm_url and romm_api_key else None

            # 3. List user files
            files, _ = crud.list_user_files(conn, user_id, limit=1000)
            logger.info(f"Found {len(files)} files for user {user_email}")

            matches = []
            for f in files:
                path = f['path']
                # Path format: platform/titleId/filename or platform/filename
                parts = path.strip("/").split("/")
                if len(parts) < 2:
                    continue

                platform = parts[0]
                title_id = parts[1] if len(parts) > 2 else None
                filename = parts[-1]

                # Try to get game name from TitleID
                game_name = None
                if title_id:
                    game_name = title_db.translate(title_id)
                
                if not game_name:
                    # Fallback to parts of the filename or path
                    game_name = title_id if title_id else parts[0]

                # Match in RomM library
                romm_id = crud.find_romm_game_for_user(conn, user_id, title_id, game_name, platform)
                
                if romm_id:
                    matches.append({
                        "path": path,
                        "game_name": game_name,
                        "romm_id": romm_id,
                        "size": f['size']
                    })
                    logger.info(f"✅ MATCH: {path} -> {game_name} (RomM ID: {romm_id})")
                else:
                    logger.warning(f"❌ NO MATCH: {path} (Estimated Name: {game_name})")

            if dry_run:
                logger.info(f"Dry-run complete. Found {len(matches)} matches.")
                return

            if not matches:
                logger.info("No matches found to push.")
                return

            # 4. Push matches
            logger.info(f"Starting push for {len(matches)} matches...")
            for m in matches:
                path = m['path']
                romm_id = m['romm_id']
                
                # Reassemble
                safe_path = os.path.abspath(os.path.join(STORAGE_DIR, str(user_id), path.lstrip("/\\")))
                if not os.path.exists(safe_path):
                    logger.error(f"File not found on disk: {safe_path}")
                    continue

                with tempfile.NamedTemporaryFile(suffix=os.path.basename(path)) as tmp:
                    try:
                        logger.info(f"Reassembling {path}...")
                        reassembly_service.reassemble_file(
                            safe_path, 
                            tmp.name, 
                            zk_key, 
                            m['size']
                        )
                        
                        logger.info(f"Pushing {path} to RomM ID {romm_id}...")
                        success = await client.upload_save(romm_id, tmp.name)
                        if success:
                            logger.info(f"🚀 Successfully pushed {path}")
                        else:
                            logger.error(f"Failed to push {path}")
                    except Exception as e:
                        logger.error(f"Error processing {path}: {e}")

    except Exception as e:
        logger.error(f"Database error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RomM Sync Test Script")
    parser.add_argument("--email", required=True, help="User email")
    parser.add_argument("--key", required=True, help="User's ZK key (Base64)")
    parser.add_argument("--push", action="store_true", help="Actually push to RomM (disable dry-run)")
    parser.add_argument("--romm-url", help="Override RomM URL")
    parser.add_argument("--romm-key", help="Override RomM API Key")
    
    args = parser.parse_args()
    
    asyncio.run(match_saves(
        args.email, 
        args.key, 
        dry_run=not args.push,
        override_romm_url=args.romm_url,
        override_romm_key=args.romm_key
    ))

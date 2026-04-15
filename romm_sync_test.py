import os
import sys
import asyncio
import argparse
import base64
import tempfile
import logging
import re

# Manual .env loader
def load_env(env_path):
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    try:
                        key, value = line.strip().split('=', 1)
                        os.environ[key] = value.strip('"').strip("'")
                    except ValueError:
                        continue

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

def clean_game_name(name):
    """Removes (USA), [!], (En,Fr,Es) and other tags for better fuzzy matching."""
    if not name: return name
    # Remove extension
    name = re.sub(r'\.[a-zA-Z0-9]+$', '', name)
    # Remove everything in parentheses or brackets
    name = re.sub(r'[\(\[][^\]\)]*[\]\)]', '', name)
    # Remove leading numbers like "02. "
    name = re.sub(r'^\d+\.\s*', '', name)
    return name.strip()

def resolve_meta_from_path(path):
    parts = path.strip("/").split("/")
    if len(parts) < 2:
        return None, None, parts[-1]

    platform = parts[0].lower()
    title_id = None
    filename = parts[-1]
    
    # Logic for nested structures like 3ds/saves/TITLEID/...
    if platform in ("3ds", "citra") and len(parts) > 2:
        if parts[1] in ("saves", "states"):
            title_id = parts[2]
        else:
            title_id = parts[1]
    
    # Logic for Switch: switch/TITLEID/filename
    elif platform in ("switch", "eden") and len(parts) >= 3:
        title_id = parts[1]

    # Logic for RetroArch: RetroArch/saves/Game Name.srm
    elif platform == "retroarch":
        # RetroArch doesn't use TitleID folders, use filename as name
        title_id = None
        
    # Logic for PSP: psp/SAVEDATA/TITLEID/...
    elif platform == "psp" and len(parts) > 2:
        if parts[1].upper() in ("SAVEDATA", "PPSSPP_STATE"):
            title_id = parts[2]
            # Strip sub-tags like "ULES01505GameData00" -> "ULES01505"
            match = re.match(r'^([A-Z]{4}\d{5})', title_id.upper())
            if match:
                title_id = match.group(1)

    return platform, title_id, filename

async def match_saves(user_email, zk_key_b64, dry_run=True, override_romm_url=None, override_romm_key=None):
    try:
        with get_db() as conn:
            user = crud.get_user_by_email(conn, user_email)
            if not user:
                logger.error(f"User {user_email} not found")
                return

            user_id = user['id']
            romm_url = override_romm_url or user.get('romm_url')
            romm_api_key = override_romm_key or user.get('romm_api_key')

            if not dry_run and (not romm_url or not romm_api_key):
                logger.error("RomM credentials missing. Cannot push.")
                return

            try:
                zk_key = base64.b64decode(zk_key_b64)
            except Exception as e:
                logger.error(f"Failed to decode ZK key: {e}")
                return

            client = RomMClient(base_url=romm_url, api_key=romm_api_key) if romm_url and romm_api_key else None

            files, _ = crud.list_user_files(conn, user_id, limit=2000)
            logger.info(f"Found {len(files)} files for user {user_email}")

            matches = []
            for f in files:
                path = f['path']
                platform, title_id, filename = resolve_meta_from_path(path)
                
                if not platform: continue

                # Step 1: Resolve Name from TitleID
                game_name = None
                if title_id:
                    game_name = title_db.translate(title_id)
                
                # Step 2: If no name from ID, use cleaned filename
                if not game_name:
                    game_name = clean_game_name(filename)

                # Step 3: Match in RomM
                # We try matching with TitleID, then Full Name, then Cleaned Name
                romm_id = crud.find_romm_game_for_user(conn, user_id, title_id, game_name, platform)
                
                # Fallback: if TitleID matching failed, try cleaned name matching
                if not romm_id and game_name:
                    cleaned = clean_game_name(game_name)
                    if cleaned != game_name:
                        romm_id = crud.find_romm_game_for_user(conn, user_id, title_id, cleaned, platform)

                if romm_id:
                    matches.append({"path": path, "game_name": game_name, "romm_id": romm_id, "size": f['size']})
                    logger.info(f"✅ MATCH: {path} -> {game_name} (RomM ID: {romm_id})")
                else:
                    logger.warning(f"❌ NO MATCH: {path} (Estimated Name: {game_name})")

            logger.info(f"\nSummary: Found {len(matches)} matches out of {len(files)} files.")
            
            if dry_run or not matches:
                return

            # Push logic...
            logger.info(f"Starting push for {len(matches)} matches...")
            for m in matches:
                # [Existing push code remains same]
                path = m['path']
                romm_id = m['romm_id']
                safe_path = os.path.abspath(os.path.join(STORAGE_DIR, str(user_id), path.lstrip("/\\")))
                if not os.path.exists(safe_path): continue

                with tempfile.NamedTemporaryFile(suffix=os.path.basename(path)) as tmp:
                    try:
                        reassembly_service.reassemble_file(safe_path, tmp.name, zk_key, m['size'])
                        success = await client.upload_save(romm_id, tmp.name)
                        if success: logger.info(f"🚀 Pushed {path}")
                    except Exception as e: logger.error(f"Error {path}: {e}")

    except Exception as e:
        logger.error(f"Database error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RomM Sync Test Script")
    parser.add_argument("--email", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--romm-url")
    parser.add_argument("--romm-key")
    args = parser.parse_args()
    asyncio.run(match_saves(args.email, args.key, not args.push, args.romm_url, args.romm_key))

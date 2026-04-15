import os
import sys
import asyncio
import argparse
import base64
import tempfile
import logging
import re
import shutil
import zipfile

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
        return None, None, parts[-1], parts[-1]

    platform = parts[0].lower()
    title_id = None
    filename = parts[-1]
    
    # Logic for nested structures like 3ds/saves/TITLEID/...
    if platform in ("3ds", "citra") and len(parts) > 2:
        if parts[1] in ("saves", "states"):
            title_id = parts[2]
            inner_path = "/".join(parts[3:])
        else:
            title_id = parts[1]
            inner_path = "/".join(parts[2:])
    
    # Logic for Switch: switch/TITLEID/filename
    elif platform in ("switch", "eden") and len(parts) >= 3:
        title_id = parts[1]
        inner_path = "/".join(parts[2:])

    # Logic for RetroArch: RetroArch/saves/Game Name.srm
    elif platform == "retroarch":
        title_id = clean_game_name(filename) # Use name as ID for grouping
        inner_path = filename
        
    # Logic for PSP: psp/SAVEDATA/TITLEID/...
    elif platform == "psp" and len(parts) > 2:
        if parts[1].upper() in ("SAVEDATA", "PPSSPP_STATE"):
            title_id_raw = parts[2]
            match = re.match(r'^([A-Z]{4}\d{5})', title_id_raw.upper())
            title_id = match.group(1) if match else title_id_raw
            inner_path = "/".join(parts[2:])
        else:
            title_id = parts[1]
            inner_path = "/".join(parts[1:])
    else:
        title_id = parts[1] if len(parts) > 1 else platform
        inner_path = "/".join(parts[1:])

    return platform, title_id, filename, inner_path

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

            files, _ = crud.list_user_files(conn, user_id, limit=3000)
            logger.info(f"Found {len(files)} files for user {user_email}")

            # Grouping Logic
            groups = {}
            for f in files:
                path = f['path']
                platform, title_id, filename, inner_path = resolve_meta_from_path(path)
                
                if not platform or not title_id: continue
                
                group_key = f"{platform}:{title_id}"
                if group_key not in groups:
                    groups[group_key] = {
                        "platform": platform,
                        "title_id": title_id,
                        "game_name": None,
                        "romm_id": None,
                        "files": []
                    }
                
                groups[group_key]["files"].append({
                    "full_path": path,
                    "inner_path": inner_path,
                    "size": f['size']
                })

            # Matching Groups
            matched_groups = []
            for gk, g in groups.items():
                # Try to resolve game name from TitleID or first filename
                game_name = None
                if g['platform'] != "retroarch":
                    game_name = title_db.translate(g['title_id'])
                
                if not game_name:
                    game_name = clean_game_name(g['files'][0]['inner_path'].split("/")[-1])

                g['game_name'] = game_name
                
                # Match in RomM
                romm_id = crud.find_romm_game_for_user(conn, user_id, g['title_id'], game_name, g['platform'])
                if not romm_id:
                    # Retry with cleaned name
                    cleaned = clean_game_name(game_name)
                    if cleaned != game_name:
                        romm_id = crud.find_romm_game_for_user(conn, user_id, g['title_id'], cleaned, g['platform'])

                if romm_id:
                    g['romm_id'] = romm_id
                    matched_groups.append(g)
                    logger.info(f"✅ MATCH: Group {gk} -> {game_name} (RomM ID: {romm_id}) | {len(g['files'])} files")
                else:
                    logger.warning(f"❌ NO MATCH: Group {gk} (Estimated Name: {game_name}) | {len(g['files'])} files")

            logger.info(f"\nSummary: Found {len(matched_groups)} matched groups out of {len(groups)} total groups.")
            
            if dry_run or not matched_groups:
                return

            # Push logic (Intelligent: Zip for folders, individual for single files)
            logger.info(f"Starting push for {len(matched_groups)} groups...")
            for g in matched_groups:
                romm_id = g['romm_id']
                game_name = g['game_name']
                platform = g['platform']
                
                # Logic: If it's RetroArch or GC, and there's only one file, don't zip.
                # Actually, RetroArch saves/states should ALWAYS be pushed individually as RomM supports these extensions.
                should_zip = platform not in ("retroarch", "gc") and len(g['files']) > 0

                if not should_zip:
                    # Individual Push
                    for gf in g['files']:
                        source_path = os.path.abspath(os.path.join(STORAGE_DIR, str(user_id), gf['full_path'].lstrip("/\\")))
                        if not os.path.exists(source_path): continue
                        
                        with tempfile.NamedTemporaryFile(suffix=os.path.basename(gf['full_path'])) as tmp:
                            try:
                                reassembly_service.reassemble_file(source_path, tmp.name, zk_key, gf['size'])
                                logger.info(f"Pushing individual file: {gf['full_path']}...")
                                success = await client.upload_save(romm_id, tmp.name)
                                if success: logger.info(f"🚀 Pushed {gf['full_path']}")
                            except Exception as e: logger.error(f"Error {gf['full_path']}: {e}")
                else:
                    # Grouped Zip Push (Switch/3DS/PSP)
                    with tempfile.TemporaryDirectory() as tmp_dir:
                        for gf in g['files']:
                            source_path = os.path.abspath(os.path.join(STORAGE_DIR, str(user_id), gf['full_path'].lstrip("/\\")))
                            if not os.path.exists(source_path): continue
                            target_path = os.path.join(tmp_dir, gf['inner_path'])
                            os.makedirs(os.path.dirname(target_path), exist_ok=True)
                            reassembly_service.reassemble_file(source_path, target_path, zk_key, gf['size'])

                        zip_name = f"{g['title_id']}_save.zip"
                        zip_path = os.path.join(tempfile.gettempdir(), zip_name)
                        try:
                            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                                for root, _, files in os.walk(tmp_dir):
                                    for file in files:
                                        abs_file = os.path.join(root, file)
                                        zf.write(abs_file, os.path.relpath(abs_file, tmp_dir))
                            
                            logger.info(f"Pushing zip archive: {game_name} ({zip_name})...")
                            success = await client.upload_save(romm_id, zip_path)
                            if success: logger.info(f"🚀 Pushed {game_name} (Zipped)")
                        finally:
                            if os.path.exists(zip_path): os.remove(zip_path)

    except Exception as e:
        logger.error(f"Database error: {e}")
        import traceback
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RomM Sync Test Script")
    parser.add_argument("--email", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--romm-url")
    parser.add_argument("--romm-key")
    args = parser.parse_args()
    asyncio.run(match_saves(args.email, args.key, not args.push, args.romm_url, args.romm_key))

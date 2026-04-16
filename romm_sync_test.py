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
import json
import subprocess

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
    name = re.sub(r'\.[a-zA-Z0-9]+$', '', name)
    name = re.sub(r'[\(\[][^\]\)]*[\]\)]', '', name)
    name = re.sub(r'^\d+\.\s*', '', name)
    return name.strip()

# --- Platform Handlers (Mirroring Argosy Kotlin Architecture) ---

class SaveHandler:
    def can_handle(self, platform: str, path: str) -> bool:
        return False
    def extract_meta(self, platform: str, path: str):
        # returns (group_key, title_id, fuzzy_name, inner_path)
        pass
    def should_zip(self, files_count: int) -> bool:
        return True
    def get_zip_name(self, title_id: str, fuzzy_name: str) -> str:
        return f"{title_id}_save.zip"

class RetroArchHandler(SaveHandler):
    def can_handle(self, platform, path):
        return platform == "retroarch"
    def extract_meta(self, platform, path):
        filename = path.split("/")[-1]
        name = clean_game_name(filename)
        return (f"retroarch:{name}", None, name, filename)
    def should_zip(self, files_count): 
        return False

class SwitchHandler(SaveHandler):
    def can_handle(self, platform, path):
        return platform in ("switch", "eden")
    def extract_meta(self, platform, path):
        parts = path.split("/")
        title_id = parts[1] if len(parts) >= 3 else parts[-1]
        inner = "/".join(parts[2:]) if len(parts) >= 3 else parts[-1]
        return (f"switch:{title_id}", title_id, None, inner)

class N3dsHandler(SaveHandler):
    def can_handle(self, platform, path):
        return platform in ("3ds", "citra")
    def extract_meta(self, platform, path):
        parts = path.split("/")
        if len(parts) > 2 and parts[1] in ("saves", "states"):
            title_id = parts[2]
            inner = "/".join(parts[3:])
        elif len(parts) > 1:
            title_id = parts[1]
            inner = "/".join(parts[2:])
        else:
            title_id = parts[-1]
            inner = parts[-1]
        return (f"3ds:{title_id}", title_id, None, inner)

class PspHandler(SaveHandler):
    def can_handle(self, platform, path):
        return platform in ("psp", "ppsspp")
    def extract_meta(self, platform, path):
        parts = path.split("/")
        if len(parts) > 2 and parts[1].upper() in ("SAVEDATA", "PPSSPP_STATE"):
            raw_id = parts[2]
            match = re.match(r'^([A-Z]{4}\d{5})', raw_id.upper())
            title_id = match.group(1) if match else raw_id
            inner = "/".join(parts[2:])
        elif len(parts) > 1:
            title_id = parts[1]
            inner = "/".join(parts[1:])
        else:
            title_id = parts[-1]
            inner = parts[-1]
        return (f"psp:{title_id}", title_id, None, inner)

class GciHandler(SaveHandler):
    def can_handle(self, platform, path):
        return platform == "gc" and path.lower().endswith(".gci")
    def extract_meta(self, platform, path):
        filename = path.split("/")[-1]
        game_id = filename[:4] 
        return (f"gc_gci:{game_id}", game_id, None, filename)
    def should_zip(self, files_count):
        return True
    def get_zip_name(self, title_id, fuzzy_name):
        return f"gci_bundle_{title_id}.zip"

class Ps2Handler(SaveHandler):
    def can_handle(self, platform, path):
        return platform in ("ps2", "aethersx2", "pcsx2")
    def extract_meta(self, platform, path):
        parts = path.split("/")
        ps2_idx = next((i for i, p in enumerate(parts) if p.lower().endswith(".ps2")), -1)
        if ps2_idx != -1:
            title_id = parts[ps2_idx].replace(".ps2", "").replace(".PS2", "")
            if ps2_idx < len(parts) - 1:
                inner = "/".join(parts[ps2_idx+1:])
                title_id = parts[ps2_idx+1]
            else:
                inner = parts[-1]
            return (f"ps2:{title_id}", title_id, None, inner)
        
        filename = parts[-1]
        name = clean_game_name(filename)
        return (f"ps2:{name}", None, name, filename)
    def should_zip(self, files_count):
        return files_count > 1

class DefaultHandler(SaveHandler):
    def can_handle(self, platform, path): return True
    def extract_meta(self, platform, path):
        parts = path.split("/")
        title_id = parts[1] if len(parts) > 1 else parts[-1]
        filename = parts[-1]
        fuzzy = clean_game_name(filename)
        return (f"{platform}:{title_id}", title_id, fuzzy, "/".join(parts[1:]))
    def should_zip(self, files_count): return files_count > 1

HANDLERS = [
    RetroArchHandler(),
    SwitchHandler(),
    N3dsHandler(),
    PspHandler(),
    GciHandler(),
    Ps2Handler(),
    DefaultHandler()
]

def resolve_meta_from_path(path):
    parts = path.strip("/").split("/")
    if len(parts) < 2:
        return None, None, parts[-1], parts[-1], DefaultHandler()
    platform = parts[0].lower()
    for handler in HANDLERS:
        if handler.can_handle(platform, path):
            group_key, title_id, fuzzy_name, inner_path = handler.extract_meta(platform, path)
            return platform, group_key, title_id, fuzzy_name, inner_path, handler
    return None, None, None, None, None, None

async def match_saves(user_email, zk_key_b64, dry_run=True, override_romm_url=None, override_romm_key=None):
    try:
        from psycopg2.extras import RealDictCursor
        with get_db() as conn:
            user = crud.get_user_by_email(conn, user_email)
            if not user:
                logger.error(f"User {user_email} not found")
                return

            user_id = user['id']
            romm_url = (override_romm_url or user.get('romm_url', '')).rstrip('/')
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
            
            # 2.5 Logic: Direct call to curl for sync to bypass Python TLS/Header 403 issues
            if args.sync_library and romm_url and romm_api_key:
                logger.info(f"Syncing RomM library via direct curl to {romm_url}...")
                try:
                    url = f"{romm_url}/api/roms?limit=5000"
                    cmd = [
                        "curl", "--location", "--silent", "--fail",
                        "--header", f"Authorization: Bearer {romm_api_key}",
                        "--header", "Accept: application/json",
                        url
                    ]
                    result = subprocess.run(cmd, capture_output=True, text=True)
                    if result.returncode == 0:
                        data = json.loads(result.stdout)
                        games = data.get('data', data.get('items', data.get('results', data)))
                        if not isinstance(games, list): games = []
                        
                        cursor = conn.cursor()
                        cursor.execute("DELETE FROM romm_games WHERE user_id = %s", (user_id,))
                        
                        count = 0
                        for g in games:
                            platform_data = g.get('platform') or {}
                            p_slug = g.get('platform_slug') or platform_data.get('slug')
                            t_id = g.get('serial') or g.get('title_id')
                            cursor.execute("""
                                INSERT INTO romm_games (user_id, romm_id, name, fs_name, platform_slug, title_id)
                                VALUES (%s, %s, %s, %s, %s, %s)
                                ON CONFLICT (user_id, romm_id) DO UPDATE SET
                                    name = EXCLUDED.name, fs_name = EXCLUDED.fs_name, 
                                    platform_slug = EXCLUDED.platform_slug, title_id = EXCLUDED.title_id
                            """, (user_id, g['id'], g['name'], g.get('file_name'), p_slug, t_id))
                            count += 1
                        conn.commit()
                        logger.info(f"✅ Successfully cached {count} games in local database.")
                    else:
                        logger.error(f"Curl failed with return code {result.returncode}: {result.stderr}")
                except Exception as e:
                    logger.error(f"Library sync failed: {e}")

            files, _ = crud.list_user_files(conn, user_id, limit=3000)
            logger.info(f"Found {len(files)} files for user {user_email}")

            groups = {}
            for f in files:
                path = f['path']
                platform, group_key, title_id, fuzzy_name, inner_path, handler = resolve_meta_from_path(path)
                if not platform or not group_key: continue
                if group_key not in groups:
                    groups[group_key] = {
                        "platform": platform, "title_id": title_id, "fuzzy_name": fuzzy_name,
                        "game_name": None, "romm_id": None, "handler": handler, "files": []
                    }
                groups[group_key]["files"].append({"full_path": path, "inner_path": inner_path, "size": f['size']})

            matched_groups = []
            for gk, g in groups.items():
                t_id = g['title_id']
                # Try name resolution from TitleDB
                game_name = title_db.translate(t_id) if t_id else g['fuzzy_name']
                if not game_name:
                    game_name = clean_game_name(g['files'][0]['inner_path'].split("/")[-1])
                g['game_name'] = game_name

                # Match in Local DB
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                query = "SELECT romm_id FROM romm_games WHERE name ILIKE %s OR fs_name ILIKE %s"
                params = [f'%{game_name}%', f'%{game_name}%']
                if t_id:
                    query += " OR title_id = %s OR name ILIKE %s OR fs_name ILIKE %s"
                    params.extend([t_id, f'%{t_id}%', f'%{t_id}%'])
                
                cursor.execute(query + " LIMIT 1", tuple(params))
                res = cursor.fetchone()
                if res:
                    g['romm_id'] = res['romm_id']
                    matched_groups.append(g)
                    logger.info(f"✅ MATCH: {g['handler'].__class__.__name__} [{gk}] -> {game_name} (ID: {res['romm_id']})")
                else:
                    logger.warning(f"❌ NO MATCH: {g['handler'].__class__.__name__} [{gk}] (Name: {game_name})")

            logger.info(f"\nSummary: Found {len(matched_groups)} matched groups out of {len(groups)} total groups.")
            
            if dry_run or not matched_groups: return

            logger.info(f"Starting push for {len(matched_groups)} groups...")
            for g in matched_groups:
                romm_id = g['romm_id']
                game_name = g['game_name']
                handler = g['handler']
                files_list = g['files']
                should_zip = handler.should_zip(len(files_list))

                if not should_zip:
                    for gf in files_list:
                        source_path = os.path.abspath(os.path.join(STORAGE_DIR, str(user_id), gf['full_path'].lstrip("/\\")))
                        if not os.path.exists(source_path): continue
                        with tempfile.NamedTemporaryFile(suffix=os.path.basename(gf['full_path'])) as tmp:
                            reassembly_service.reassemble_file(source_path, tmp.name, zk_key, gf['size'])
                            success = await client.upload_save(romm_id, tmp.name)
                            if success: logger.info(f"🚀 Pushed {gf['full_path']}")
                else:
                    with tempfile.TemporaryDirectory() as tmp_dir:
                        for gf in files_list:
                            source_path = os.path.abspath(os.path.join(STORAGE_DIR, str(user_id), gf['full_path'].lstrip("/\\")))
                            if not os.path.exists(source_path): continue
                            target_path = os.path.join(tmp_dir, gf['inner_path'])
                            os.makedirs(os.path.dirname(target_path), exist_ok=True)
                            reassembly_service.reassemble_file(source_path, target_path, zk_key, gf['size'])

                        zip_name = handler.get_zip_name(g['title_id'] or "save", game_name)
                        zip_path = os.path.join(tempfile.gettempdir(), zip_name)
                        try:
                            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                                for root, _, files in os.walk(tmp_dir):
                                    for file in files:
                                        abs_f = os.path.join(root, file)
                                        zf.write(abs_f, os.path.relpath(abs_f, tmp_dir))
                            success = await client.upload_save(romm_id, zip_path)
                            if success: logger.info(f"🚀 Pushed {game_name} (Zipped)")
                        finally:
                            if os.path.exists(zip_path): os.remove(zip_path)
    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RomM Sync Test Script")
    parser.add_argument("--email", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--romm-url")
    parser.add_argument("--romm-key")
    parser.add_argument("--sync-library", action="store_true")
    args = parser.parse_args()
    asyncio.run(match_saves(args.email, args.key, not args.push, args.romm_url, args.romm_key))

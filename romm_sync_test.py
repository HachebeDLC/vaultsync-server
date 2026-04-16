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
        return False # RetroArch files are uploaded individually

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
        game_id = filename[:4] # GameCube IDs usually start with 4-6 chars (e.g. GM4E)
        return (f"gc_gci:{game_id}", game_id, None, filename)
    def should_zip(self, files_count):
        return True # GciSaveHandler uses createBundle() to zip GCIs
    def get_zip_name(self, title_id, fuzzy_name):
        return f"gci_bundle_{title_id}.zip"

class Ps2Handler(SaveHandler):
    def can_handle(self, platform, path):
        return platform in ("ps2", "aethersx2", "pcsx2")
    def extract_meta(self, platform, path):
        parts = path.split("/")
        # Look for the .ps2 folder or file
        ps2_idx = next((i for i, p in enumerate(parts) if p.lower().endswith(".ps2")), -1)
        if ps2_idx != -1:
            title_id = parts[ps2_idx].replace(".ps2", "").replace(".PS2", "")
            # If it has subdirectories (folder memory card), inner is everything inside it
            if ps2_idx < len(parts) - 1:
                inner = "/".join(parts[ps2_idx+1:])
                # The serial is usually the folder inside the memcard
                serial_folder = parts[ps2_idx+1]
                title_id = serial_folder
            else:
                inner = parts[-1]
            return (f"ps2:{title_id}", title_id, None, inner)
        
        filename = parts[-1]
        name = clean_game_name(filename)
        return (f"ps2:{name}", None, name, filename)
    def should_zip(self, files_count):
        return files_count > 1 # Only zip if it's a folder memory card with multiple files

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
    DefaultHandler() # Must be last
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

            # Initialize RomM client for this user
            client = RomMClient(base_url=romm_url, api_key=romm_api_key) if romm_url and romm_api_key else None
            
            # Request Headers matching user's successful curl exactly
            curl_headers = {
                "Authorization": f"Bearer {romm_api_key}",
                "User-Agent": "curl/7.81.0",
                "Accept": "*/*"
            }

            # 2.5 Optional: Sync RomM Library to local DB
            if args.sync_library and client:
                logger.info(f"Syncing RomM library to local database from {romm_url}...")
                try:
                    import httpx
                    with httpx.Client(base_url=romm_url, verify=False, timeout=60.0, follow_redirects=True) as http:
                        resp = http.get("/api/roms", headers=curl_headers) # Removed limit=1000 to keep it clean as per user req
                        if resp.status_code == 200:
                            data = resp.json()
                            games = data.get('data', data.get('items', data.get('results', data)))
                            if not isinstance(games, list):
                                games = [games] if isinstance(games, dict) and 'id' in games else []
                            
                            cursor = conn.cursor()
                            
                            # Ensure the table has the correct schema including title_id
                            cursor.execute('''
                                CREATE TABLE IF NOT EXISTS romm_games (
                                    id SERIAL PRIMARY KEY,
                                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                                    romm_id INTEGER NOT NULL,
                                    name TEXT NOT NULL,
                                    fs_name TEXT,
                                    platform_slug TEXT,
                                    title_id TEXT,
                                    UNIQUE(user_id, romm_id)
                                )
                            ''')
                            # Migration: Add title_id if it's an old table
                            cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='romm_games' AND column_name='title_id'")
                            if not cursor.fetchone():
                                cursor.execute("ALTER TABLE romm_games ADD COLUMN title_id TEXT")
                            
                            cursor.execute("DELETE FROM romm_games WHERE user_id = %s", (user_id,))
                            
                            count = 0
                            for g in games:
                                platform_data = g.get('platform') or {}
                                p_slug = g.get('platform_slug') or platform_data.get('slug')
                                # Try to find a TitleID/Serial in the RomM record
                                t_id = g.get('serial') or g.get('title_id')
                                
                                cursor.execute("""
                                    INSERT INTO romm_games (user_id, romm_id, name, fs_name, platform_slug, title_id)
                                    VALUES (%s, %s, %s, %s, %s, %s)
                                """, (user_id, g['id'], g['name'], g.get('file_name'), p_slug, t_id))
                                count += 1
                            conn.commit()
                            logger.info(f"✅ Successfully cached {count} games in local database.")
                        else:
                            logger.error(f"Failed to fetch library: HTTP {resp.status_code}")
                except Exception as e:
                    logger.error(f"Library sync failed: {e}")

            # Fetch library for fallback matching (Once per run)
            romm_games_api = []
            api_failed = False
            
            def load_api_library():
                nonlocal romm_games_api, api_failed
                if romm_games_api or api_failed: return
                logger.info("Fetching library from RomM API for fallback matching...")
                try:
                    import httpx
                    with httpx.Client(base_url=romm_url, verify=False, timeout=30.0, follow_redirects=True) as http:
                        resp = http.get("/api/roms", headers=curl_headers)
                        if resp.status_code == 200:
                            data = resp.json()
                            romm_games_api = data.get('data', data.get('items', [])) if isinstance(data, dict) else data
                        else:
                            api_failed = True
                except Exception:
                    api_failed = True

            def find_romm_game(title_id, game_name, platform_slug):
                # 1. Try Local DB (Primary - searching name, filename AND title_id column)
                try:
                    cursor = conn.cursor(cursor_factory=RealDictCursor)
                    query = "SELECT romm_id FROM romm_games WHERE name ILIKE %s OR fs_name ILIKE %s"
                    params = [f'%{game_name}%', f'%{game_name}%']
                    
                    if title_id:
                        query += " OR title_id = %s OR name ILIKE %s OR fs_name ILIKE %s"
                        params.extend([title_id, f'%{title_id}%', f'%{title_id}%'])
                    
                    cursor.execute(query + " LIMIT 1", tuple(params))
                    res = cursor.fetchone()
                    if res: return res['romm_id']
                except Exception as e:
                    logger.debug(f"Local DB match error: {e}")

                # 2. Try API (Fallback)
                if not client or api_failed: return None
                load_api_library()
                
                if not romm_games_api: return None
                
                # Normalize search terms
                search_terms = []
                if title_id: search_terms.append(title_id.lower())
                if game_name: search_terms.append(clean_game_name(game_name).lower())
                
                for g in romm_games_api:
                    g_name = g.get('name', '').lower()
                    f_name = g.get('file_name', '').lower()
                    p_slug = g.get('platform_slug', g.get('platform', {}).get('slug', '')).lower()
                    
                    if platform_slug and platform_slug.lower() not in p_slug:
                        continue
                        
                    for term in search_terms:
                        if term in g_name or term in f_name:
                            return g.get('id')
                return None

            files, _ = crud.list_user_files(conn, user_id, limit=3000)
            logger.info(f"Found {len(files)} files for user {user_email}")

            # Grouping Logic
            groups = {}
            for f in files:
                path = f['path']
                platform, group_key, title_id, fuzzy_name, inner_path, handler = resolve_meta_from_path(path)
                
                if not platform or not group_key: continue
                
                if group_key not in groups:
                    groups[group_key] = {
                        "platform": platform,
                        "title_id": title_id,
                        "fuzzy_name": fuzzy_name,
                        "game_name": None,
                        "romm_id": None,
                        "handler": handler,
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
                game_name = g['fuzzy_name']
                if not game_name and g['title_id'] and g['platform'] != "retroarch":
                    game_name = title_db.translate(g['title_id'])
                
                if not game_name:
                    game_name = clean_game_name(g['files'][0]['inner_path'].split("/")[-1])

                g['game_name'] = game_name
                
                # Match against live RomM data
                romm_id = find_romm_game(g['title_id'], game_name, g['platform'])
                if not romm_id and game_name:
                    cleaned = clean_game_name(game_name)
                    if cleaned != game_name:
                        romm_id = find_romm_game(g['title_id'], cleaned, g['platform'])

                if romm_id:

                    g['romm_id'] = romm_id
                    matched_groups.append(g)
                    logger.info(f"✅ MATCH: {g['handler'].__class__.__name__} [{gk}] -> {game_name} (RomM ID: {romm_id}) | {len(g['files'])} files")
                else:
                    logger.warning(f"❌ NO MATCH: {g['handler'].__class__.__name__} [{gk}] (Estimated Name: {game_name}) | {len(g['files'])} files")

            logger.info(f"\nSummary: Found {len(matched_groups)} matched groups out of {len(groups)} total groups.")
            
            if dry_run or not matched_groups:
                return

            # Push logic based on Handlers
            logger.info(f"Starting push for {len(matched_groups)} groups...")
            for g in matched_groups:
                romm_id = g['romm_id']
                game_name = g['game_name']
                handler = g['handler']
                files_list = g['files']
                
                should_zip = handler.should_zip(len(files_list))

                if not should_zip:
                    # Individual Push (RetroArch, single files)
                    for gf in files_list:
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
                    # Grouped Zip Push (Switch, 3DS, PSP, GCI Bundles)
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
    parser.add_argument("--sync-library", action="store_true", help="Sync library from RomM API into local database before matching")
    args = parser.parse_args()
    asyncio.run(match_saves(args.email, args.key, not args.push, args.romm_url, args.romm_key))

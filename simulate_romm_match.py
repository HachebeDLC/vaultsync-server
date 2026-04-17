import sys
import os
import re
import asyncio
import httpx
from dotenv import load_dotenv

# Setup path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

ROMM_URL = os.environ.get("ROMM_URL", "https://romm.artemisa-hb.cloud")
ROMM_API_KEY = os.environ.get("ROMM_API_KEY")

from services.title_db_service import title_db

# The user's save tree
TEST_PATHS = [
    "3ds/saves/00030700/data/00000001/system3.dat",
    "3ds/saves/00033c00/data/00000001/figure.sav",
    "RetroArch/saves/02. Chrono Trigger (USA).srm",
    "RetroArch/saves/Apotris-v4.0.2GBA.srm",
    "RetroArch/states/12. Mega Man X (USA) (Rev 1).state.auto",
    "dc/memcards/Mcd001.ps2",
    "gc/GBA/02. Fire Emblem (USA, Australia).srm",
    "gc/GBA/Apotris.srm",
    "gc/GM4E.gci",
    "ps2/Mcd001.ps2",
    "psp/SAVEDATA/NPUG80329DATA00/ICON0.PNG",
    "switch/01007300020FA000/GameData.dat",
    "wii/00010000/52334d45/data/save.bin"
]

async def fetch_romm_library():
    print(f"Fetching library from {ROMM_URL}...")
    headers = {"Authorization": f"Bearer {ROMM_API_KEY}"}
    all_games = []
    offset = 0
    limit = 1000
    
    async with httpx.AsyncClient() as client:
        while True:
            resp = await client.get(
                f"{ROMM_URL}/api/roms", 
                params={"limit": limit, "offset": offset},
                headers=headers, 
                timeout=30.0
            )
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                if not items:
                    break
                all_games.extend(items)
                offset += limit
                if len(items) < limit:
                    break
            else:
                print(f"Failed to fetch RomM library: HTTP {resp.status_code}")
                break
    print(f"Downloaded {len(all_games)} games from RomM.\n")
    return all_games

def simulate_local_match(path, all_games):
    parts = path.split("/")
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

    print(f"--- PATH: {path} ---")
    print(f"  Parsed Platform: {romm_platform}")
    print(f"  Parsed Target ID: {target_id}")
    print(f"  Parsed Clean Name: {clean_target}")

    # Simulate DB search
    for g in all_games:
        g_name = g.get('name', '')
        g_fs = g.get('fs_name', '')
        g_plat = g.get('platform', {}).get('slug', '')
        
        # Exact ID match
        if target_id:
            if (target_id.lower() in g_name.lower() or target_id.lower() in g_fs.lower()):
                if not romm_platform or g_plat == romm_platform:
                    print(f"  ✅ MATCHED (by ID): {g_name} [RommID: {g['id']}]")
                    return
                    
        # Fuzzy Name match
        if clean_target:
            if (clean_target.lower() in g_name.lower() or g_name.lower() in clean_target.lower() or 
                clean_target.lower() in g_fs.lower() or g_fs.lower() in clean_target.lower()):
                if not romm_platform or g_plat == romm_platform:
                    print(f"  ✅ MATCHED (by Name): {g_name} [RommID: {g['id']}]")
                    return
                    
    print(f"  ❌ FAILED TO MATCH")

async def main():
    all_games = await fetch_romm_library()
    if not all_games:
        return
        
    for path in TEST_PATHS:
        simulate_local_match(path, all_games)
        print()

if __name__ == "__main__":
    asyncio.run(main())

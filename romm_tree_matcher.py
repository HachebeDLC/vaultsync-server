import subprocess
import json
import os
import re
import sys

# Corrected credentials from your curl request
ROMM_URL = "https://romm.artemisa-hb.cloud"
ROMM_API_KEY = "rmm_a08078eff030c78745706a60bf0c0fb7cd358d4f9a78c9b3e8a1439a0e2d83a0"
ROMM_COOKIE = "romm_csrftoken=.eJwFwcFugjAAANB_6XkkpbTAvIHgigI6VBAvJpailYYOpGyw-O--9w8G1fAWLMAWXdnz3B17RQ9yonGRr8JbKfDoWsoiyp8IMWrz27t3QRfr-WK2cG_EAd34cN4W42O5Y6t1MD8ip3GF8yUqRLldY51p7-_HiMwpxWK3-YWMnNxwDDPioXXgNCK61XdfltEn91Iy5DKv8JCUNkOW6q-F3aCldlMoqAlxx4eQnZMEfAD95P1FVGDRailfbwswQFI.KBDXuhCn1SgF9nt3Nl7r8QBKqFI"

# The exact save tree you provided
SAVE_TREE = [
    "3ds/saves/00030700/data/00000001/system3.dat",
    "3ds/saves/00033600/data/00000001/save00.bin",
    "3ds/saves/00033c00/data/00000001/figure.sav",
    "RetroArch/saves/02. Chrono Trigger (USA).srm",
    "RetroArch/saves/02. Fire Emblem (USA, Australia).srm",
    "RetroArch/saves/Apotris-v4.0.2GBA.srm",
    "RetroArch/states/12. Mega Man X (USA) (Rev 1).state.auto",
    "dc/memcards/Mcd001.ps2",
    "gc/GM4E.gci",
    "ps2/Mcd001.ps2",
    "psp/SAVEDATA/ULES01505GameData00/PARAM.SFO",
    "switch/01007300020FA000/GameData.dat",
    "switch/0100F2C0115B6000/album/000_Photo.jpg",
    "wii/00010000/52334d45/data/save.bin"
]

# CORRECTED ID TRANSLATIONS
KNOWN_IDS = {
    "01007300020FA000": "Astral Chain",
    "0100F2C0115B6000": "The Legend of Zelda: Tears of the Kingdom",
    "00030700": "Mario Kart 7",
    "00033C00": "Super Street Fighter IV 3D",
    "00033600": "Super Smash Bros",
    "ULES01505": "Dissidia 012",
    "GM4E": "Super Mario Sunshine",
    "52334D45": "Mario Kart Wii"
}

def fetch_romm_library(url, key, cookie):
    print(f"Fetching ROMs from {url} using curl...")
    cmd = [
        "curl", "-s", "--location", f"{url.rstrip('/')}/api/roms?limit=1000",
        "--header", f"Authorization: Bearer {key}",
        "--header", f"Cookie: {cookie}"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            items = data.get("items", [])
            print(f"✅ Successfully downloaded {len(items)} games from RomM.\n")
            return items
        except json.JSONDecodeError:
            print("❌ Failed to parse JSON.")
            return []
    return []

def simulate_match(path, romm_library):
    parts = path.split("/")
    platform = parts[0].lower()
    filename = parts[-1]
    
    target_id = None
    target_name = filename
    
    # ID Extraction
    if platform in ('switch', 'eden') and len(parts) >= 3:
        target_id = parts[1].upper()
    elif platform in ('psp', 'ppsspp') and len(parts) >= 3 and parts[1].upper() == 'SAVEDATA':
        target_id = parts[2][:9].upper() 
    elif platform == '3ds' and len(parts) >= 3 and parts[1].lower() == 'saves':
        target_id = parts[2].upper()
    elif platform == 'wii' and len(parts) >= 3 and parts[1] == '00010000':
        target_id = parts[2].upper()
    elif platform in ('gc', 'dolphin'):
        if filename.lower().endswith('.gci'):
            target_id = filename.split('.')[0].upper()
            
    # Translate ID
    translated_name = None
    if target_id and target_id in KNOWN_IDS:
        translated_name = KNOWN_IDS[target_id]
        target_name = translated_name
        
    # Clean term
    clean_target = os.path.splitext(target_name)[0]
    clean_target = re.sub(r'^\d+\.\s*', '', clean_target).lower().strip()
    
    print(f"[{platform.upper()}] Path: {path}")
    if target_id:
        print(f" ├─ Extracted ID: {target_id}")
    if translated_name:
        print(f" ├─ Translated via DB: '{translated_name}'")
        
    match_found = False
    for game in romm_library:
        g_name = game.get('name', '').lower()
        g_fs = game.get('fs_name', '').lower()
        
        # Priority 1: Match by Translated Name
        if translated_name:
            if clean_target in g_name or clean_target in g_fs or g_name in clean_target:
                print(f" └─ ✅ MATCHED (by Name): {game['name']} (RomM ID: {game['id']})\n")
                match_found = True
                break
            continue

        # Priority 2: Match by exact ID substring
        game_str = str(game).upper()
        if target_id and target_id in game_str:
            print(f" └─ ✅ MATCHED (ID Substring): {game['name']} (RomM ID: {game['id']})\n")
            match_found = True
            break
            
        # Priority 3: Fuzzy Name match
        if clean_target and (clean_target in g_name or clean_target in g_fs or g_name in clean_target):
            print(f" └─ ✅ MATCHED (Fuzzy Name): {game['name']} (RomM ID: {game['id']})\n")
            match_found = True
            break
            
    if not match_found:
        print(f" └─ ❌ NO MATCH IN ROMM\n")

if __name__ == "__main__":
    library = fetch_romm_library(ROMM_URL, ROMM_API_KEY, ROMM_COOKIE)
    if library:
        for p in SAVE_TREE:
            simulate_match(p, library)

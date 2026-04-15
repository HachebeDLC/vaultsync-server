import sys
import os
import psycopg2
from psycopg2.extras import RealDictCursor
import re
from dotenv import load_dotenv

# Load env
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_NAME = os.environ.get("DB_NAME", "vaultsync")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASS = os.environ.get("DB_PASS", "postgres")

# Mock user ID
USER_ID = 1

def connect_db():
    return psycopg2.connect(host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASS)

def test_match(path: str):
    from app.services.title_db_service import title_db
    
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

    print(f"\nPath: {path}")
    print(f"-> Platform: {romm_platform}")
    print(f"-> Target ID: {target_id}")
    print(f"-> Translated Name: {translated_name}")
    print(f"-> Clean Search Term: '{clean_target}'")
    
    try:
        conn = connect_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Exact ID Match (for Switch, GC, PSP)
        if target_id:
            query = "SELECT romm_id, name, platform_slug FROM romm_games WHERE user_id = %s AND (name ILIKE %s OR fs_name ILIKE %s)"
            params = [USER_ID, f'%{target_id}%', f'%{target_id}%']
            if romm_platform:
                query += " AND platform_slug = %s"
                params.append(romm_platform)
            query += " LIMIT 1"
            
            cursor.execute(query, tuple(params))
            res = cursor.fetchone()
            if res: 
                print(f"✅ MATCH (ID): {res['name']} (RomM ID: {res['romm_id']}, Platform: {res['platform_slug']})")
                return

        # Fuzzy Name Match
        if clean_target:
            query = "SELECT romm_id, name, platform_slug FROM romm_games WHERE user_id = %s AND (name ILIKE %s OR fs_name ILIKE %s)"
            params = [USER_ID, f'%{clean_target}%', f'%{clean_target}%']
            if romm_platform:
                query += " AND platform_slug = %s"
                params.append(romm_platform)
            query += " LIMIT 1"
            
            cursor.execute(query, tuple(params))
            res = cursor.fetchone()
            if res: 
                print(f"✅ MATCH (Fuzzy): {res['name']} (RomM ID: {res['romm_id']}, Platform: {res['platform_slug']})")
                return
                
        print("❌ NO MATCH FOUND IN DATABASE")
    except Exception as e:
        print(f"Error querying DB: {e}")
    finally:
        if 'conn' in locals() and conn:
            conn.close()

if __name__ == "__main__":
    print("=== VaultSync Local Match Tester ===")
    test_match("gc/GBA/02. Fire Emblem (USA, Australia).srm")
    test_match("gc/GBA/Apotris.srm")
    test_match("gc/GBA/Pokemon - Emerald Version (USA, Europe).srm")
    test_match("gc/GM4E.gci")

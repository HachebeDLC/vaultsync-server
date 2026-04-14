import os
import psycopg2
from app.config import DB_HOST, DB_NAME, DB_USER, DB_PASS, STORAGE_DIR

# Map games to their likely cores based on common usage
CORE_MAPPING = {
    '02. Chrono Trigger': 'Snes9x',
    '02. Fire Emblem': 'mGBA',
    '08. The Legend of Zelda - A Link to the Past': 'mGBA',
    '12. Mega Man X': 'Snes9x',
    '47. Kirby & the Amazing Mirror': 'mGBA',
    '53. Wario Land 4': 'mGBA',
    '67. Dragon Ball Z - The Legacy of Goku I & II': 'mGBA',
    'Apotris': 'mGBA',
    'Castlevania - Symphony of the Night': 'PCSX-ReARMed',
    'ChuChu Rocket!': 'mGBA',
    'Kirby - Nightmare in Dream Land': 'mGBA',
    'Mario Kart DS': 'melonDS', # Assuming melonDS or DeSmuME
    'Mega Man X': 'Snes9x',
    'Mega Man Zero': 'mGBA',
    'Metroid Fusion': 'mGBA',
    'Nintendogs - Dalmatian & Friends': 'melonDS',
    'PaRappa the Rapper': 'PCSX-ReARMed',
    'Pokemon - Emerald Version': 'mGBA',
    'Pokemon - HeartGold Version': 'melonDS',
    'Pokemon Pinball': 'Gambatte',
    'Profesor Layton y la Caja de Pandora, El': 'melonDS',
    'Tekken 3': 'PCSX-ReARMed',
    'Tony Hawk\'s Pro Skater': 'PCSX-ReARMed',
    'Wario Land 4': 'mGBA',
    'WarioWare - Touched!': 'melonDS'
}

def guess_core(filename):
    for key, core in CORE_MAPPING.items():
        if key in filename:
            return core
    return 'UnknownCore' # Fallback

def migrate_retroarch_saves():
    conn = psycopg2.connect(host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
    conn.autocommit = True 
    cursor = conn.cursor()
    
    print("Finding RetroArch files to migrate...")
    cursor.execute("SELECT id, user_id, path FROM files WHERE path ILIKE 'RetroArch/%'")
    files = cursor.fetchall()
    
    migrated_count = 0
    dropped_count = 0
    
    for file_id, user_id, old_path in files:
        parts = old_path.split('/')
        # Skip if already perfectly nested: RetroArch/saves/CoreName/game.srm
        if len(parts) >= 4 and (parts[1] == 'saves' or parts[1] == 'states'):
            continue
            
        filename = parts[-1]
        
        is_state = '.state' in filename.lower() or filename.lower().endswith('.png')
        folder = 'states' if is_state else 'saves'
        
        core_folder = guess_core(filename)
        
        # RetroArch/saves/mGBA/game.srm
        new_path = f"RetroArch/{folder}/{core_folder}/{filename}"
        
        old_physical_path = os.path.join(STORAGE_DIR, str(user_id), old_path.lstrip("/\\"))
        new_physical_path = os.path.join(STORAGE_DIR, str(user_id), new_path.lstrip("/\\"))
        
        if os.path.exists(old_physical_path):
            os.makedirs(os.path.dirname(new_physical_path), exist_ok=True)
            if not os.path.exists(new_physical_path):
                os.rename(old_physical_path, new_physical_path)
                print(f"Moved: {old_path} -> {new_path}")
            else:
                print(f"File already exists physically at {new_path}. Deleting old {old_path}")
                os.remove(old_physical_path)
        
        try:
            cursor.execute("UPDATE files SET path = %s WHERE id = %s", (new_path, file_id))
            migrated_count += 1
        except psycopg2.errors.UniqueViolation:
            print(f"Warning: {new_path} already exists in DB. Dropping old record {old_path}.")
            cursor.execute("DELETE FROM files WHERE id = %s", (file_id,))
            dropped_count += 1
        
    cursor.close()
    conn.close()
    
    print(f"\nMigration complete. Moved {migrated_count} files. Dropped {dropped_count} duplicates.")

if __name__ == "__main__":
    migrate_retroarch_saves()

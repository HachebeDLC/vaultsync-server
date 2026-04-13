import os
import psycopg2
from app.config import DB_HOST, DB_NAME, DB_USER, DB_PASS, STORAGE_DIR

def migrate_retroarch_saves():
    conn = psycopg2.connect(host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
    cursor = conn.cursor()
    
    print("Finding RetroArch files to migrate...")
    cursor.execute("SELECT id, user_id, path FROM files WHERE path ILIKE 'RetroArch/%'")
    files = cursor.fetchall()
    
    migrated_count = 0
    
    for file_id, user_id, old_path in files:
        # Check if it already has the correct folder structure
        parts = old_path.split('/')
        if len(parts) >= 3 and (parts[1] == 'saves' or parts[1] == 'states'):
            continue
            
        filename = parts[-1]
        
        # Determine new folder
        is_state = '.state' in filename.lower() or filename.lower().endswith('.png')
        folder = 'states' if is_state else 'saves'
        
        # Fix the system prefix casing just in case
        system_prefix = parts[0]
        if system_prefix.lower() == 'retroarch':
            system_prefix = 'RetroArch'
            
        new_path = f"{system_prefix}/{folder}/{filename}"
        
        # Update physical file location
        old_physical_path = os.path.join(STORAGE_DIR, str(user_id), old_path.lstrip("/\\"))
        new_physical_path = os.path.join(STORAGE_DIR, str(user_id), new_path.lstrip("/\\"))
        
        if os.path.exists(old_physical_path):
            os.makedirs(os.path.dirname(new_physical_path), exist_ok=True)
            os.rename(old_physical_path, new_physical_path)
            print(f"Moved: {old_path} -> {new_path}")
        else:
            print(f"Warning: Physical file not found for {old_path}, updating DB only.")
            
        # Update database
        cursor.execute("UPDATE files SET path = %s WHERE id = %s", (new_path, file_id))
        migrated_count += 1
        
    conn.commit()
    cursor.close()
    conn.close()
    
    print(f"\nMigration complete. Moved {migrated_count} files.")

if __name__ == "__main__":
    migrate_retroarch_saves()

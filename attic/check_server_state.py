import sys
import os
import psycopg2

sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))
from app.config import DB_HOST, DB_NAME, DB_USER, DB_PASS
from app.services.title_db_service import title_db

def check_state():
    print(f"TitleDB loaded {len(title_db.db)} entries.")
    try:
        conn = psycopg2.connect(host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM users")
        users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM files")
        files = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM romm_games")
        romm_games = cursor.fetchone()[0]
        
        print(f"Users in DB: {users}")
        print(f"Files in DB: {files}")
        print(f"RomM Games in DB: {romm_games}")
        
        if romm_games > 0:
            cursor.execute("SELECT name, fs_name, platform_slug FROM romm_games LIMIT 5")
            print("Sample RomM Games:")
            for row in cursor.fetchall():
                print(f" - {row}")
                
    except Exception as e:
        print(f"DB Error: {e}")

if __name__ == "__main__":
    check_state()

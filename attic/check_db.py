import psycopg2
from psycopg2.extras import RealDictCursor
import os

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "vaultsync")
DB_USER = os.environ.get("DB_USER", "vaultsync")
DB_PASS = os.environ.get("DB_PASS", "")

def check_files():
    try:
        # Try to connect to the DB (might need to run inside docker or via exposed port)
        conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASS)
        c = conn.cursor(cursor_factory=RealDictCursor)
        
        print("--- REGISTERED FILES ---")
        c.execute("SELECT id, user_id, path, size, device_name, updated_at FROM files ORDER BY path")
        rows = c.fetchall()
        for row in rows:
            print(f"ID: {row['id']} | Path: {row['path']} | Size: {row['size']} | Device: {row['device_name']}")
            
        print("\n--- USERS ---")
        c.execute("SELECT id, email FROM users")
        users = c.fetchall()
        for u in users:
            print(f"ID: {u['id']} | Email: {u['email']}")
            
        conn.close()
    except Exception as e:
        print(f"Error connecting to DB: {e}")
        print("Tip: If running on host, ensure the DB port is exposed in docker-compose.yaml or run this script via: docker exec -it vaultsync_server python3 check_db.py")

if __name__ == "__main__":
    check_files()

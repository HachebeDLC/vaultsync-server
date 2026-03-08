import unittest
import psycopg2
import os

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_NAME = os.environ.get("DB_NAME", "vaultsync")
DB_USER = os.environ.get("DB_USER", "vaultsync")
DB_PASS = os.environ.get("DB_PASS", "vaultsync_password")

class TestDBSchema(unittest.TestCase):
    def test_recovery_columns_exist(self):
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        cur = conn.cursor()
        
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='recovery_payload'")
        self.assertIsNotNone(cur.fetchone(), "recovery_payload column missing")
        
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='recovery_salt'")
        self.assertIsNotNone(cur.fetchone(), "recovery_salt column missing")
        
        cur.close()
        conn.close()

if __name__ == "__main__":
    unittest.main()

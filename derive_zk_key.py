import os
import sys
import argparse
import base64
import logging
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend

# Manual .env loader
def load_env(env_path):
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    key, value = line.strip().split('=', 1)
                    os.environ[key] = value.strip('"').strip("'")

# Try to load from vaultsync_server/.env or current dir
load_env(os.path.join(os.getcwd(), "vaultsync_server", ".env"))
load_env(".env")

# Add vaultsync_server to path
sys.path.append(os.path.join(os.getcwd(), "vaultsync_server"))

from app.database import get_db
from app import crud

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("DeriveKey")

def derive_key(password, salt_hex):
    salt = bytes.fromhex(salt_hex)
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
        backend=default_backend()
    )
    key = kdf.derive(password.encode())
    return base64.b64encode(key).decode()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Derive Zero-Knowledge Master Key")
    parser.add_argument("--email", required=True, help="User email to fetch salt")
    parser.add_argument("--password", required=True, help="User password")
    
    args = parser.parse_args()
    
    try:
        with get_db() as conn:
            user = crud.get_user_by_email(conn, args.email)
            if not user:
                print(f"Error: User {args.email} not found")
                sys.exit(1)
            
            salt = user.get('salt')
            if not salt:
                print(f"Error: User {args.email} has no salt in database")
                sys.exit(1)
            
            key = derive_key(args.password, salt)
            print("-" * 40)
            print(f"ZK Master Key for {args.email}:")
            print(key)
            print("-" * 40)
            print("\nYou can use this key with --key in the romm_sync_test.py script.")
            
    except Exception as e:
        print(f"Database error: {e}")
        sys.exit(1)

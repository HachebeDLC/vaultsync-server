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

def derive_key(password, salt_value):
    # Fallback logic: if salt looks like hex (32 chars), use bytes.fromhex
    # Otherwise, use the raw string (like an email) as the salt bytes.
    try:
        if len(salt_value) == 32 and all(c in "0123456789abcdefABCDEF" for c in salt_value):
            salt_bytes = bytes.fromhex(salt_value)
        else:
            salt_bytes = salt_value.encode()
    except Exception:
        salt_bytes = salt_value.encode()

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt_bytes,
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
            
            # Use DB salt if available, otherwise fallback to email (VaultSync legacy fallback)
            salt = user.get("salt") or user["email"]
            
            key = derive_key(args.password, salt)
            print("-" * 40)
            print(f"ZK Master Key for {args.email}:")
            print(key)
            print("-" * 40)
            print("\nYou can use this key with --key in the romm_sync_test.py script.")
            
    except Exception as e:
        print(f"Database error: {e}")
        sys.exit(1)

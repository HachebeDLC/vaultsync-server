import os

# --- Security & Auth ---
SECRET_KEY = os.environ.get("VAULTSYNC_SECRET", "CHANGE_THIS_IN_PROD")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30
ACCESS_TOKEN_EXPIRE_MINUTES = ACCESS_TOKEN_EXPIRE_DAYS * 24 * 60

# --- Storage ---
STORAGE_DIR = os.path.abspath("storage")
os.makedirs(STORAGE_DIR, exist_ok=True)

# --- Database ---
DB_HOST = os.environ.get("DB_HOST", "db")
DB_NAME = os.environ.get("DB_NAME", "vaultsync")
DB_USER = os.environ.get("DB_USER", "vaultsync")
DB_PASS = os.environ.get("DB_PASS", "vaultsync_password")

# --- Block Protocol ---
BLOCK_SIZE = 1024 * 1024  # 1MB Plaintext
OVERHEAD = 7 + 16 + 16    # Magic (7) + IV (16) + Padding (16)
ENCRYPTED_BLOCK_SIZE = BLOCK_SIZE + OVERHEAD

# --- CORS ---
CORS_ORIGINS = os.environ.get("VAULTSYNC_CORS_ORIGINS", "*").split(",")

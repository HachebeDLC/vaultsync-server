import os

# --- Security & Auth ---
SECRET_KEY = os.environ.get("VAULTSYNC_SECRET")
if not SECRET_KEY:
    raise ValueError("VAULTSYNC_SECRET environment variable is missing!")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 30

# --- Storage ---
STORAGE_DIR = os.path.abspath("storage")
os.makedirs(STORAGE_DIR, exist_ok=True)

# --- Database ---
DB_HOST = os.environ.get("DB_HOST", "db")
DB_NAME = os.environ.get("DB_NAME", "vaultsync")
DB_USER = os.environ.get("DB_USER", "vaultsync")
DB_PASS = os.environ.get("DB_PASS", "vaultsync_password")

# --- Redis ---
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))

# --- Block Protocol ---
SMALL_BLOCK_SIZE = 256 * 1024     # 256KB Plaintext
LARGE_BLOCK_SIZE = 1024 * 1024    # 1MB Plaintext
BLOCK_THRESHOLD = 10 * 1024 * 1024 # 10MB Threshold
OVERHEAD = 7 + 16 + 16            # Magic (7) + IV (16) + Padding (16)

def get_block_size(file_size: int) -> int:
    return LARGE_BLOCK_SIZE if file_size >= BLOCK_THRESHOLD else SMALL_BLOCK_SIZE

def get_encrypted_block_size(file_size: int) -> int:
    return get_block_size(file_size) + OVERHEAD

# Backwards compatibility / defaults
BLOCK_SIZE = LARGE_BLOCK_SIZE
ENCRYPTED_BLOCK_SIZE = BLOCK_SIZE + OVERHEAD

# --- CORS ---
CORS_ORIGINS = os.environ.get("VAULTSYNC_CORS_ORIGINS", "*").split(",")

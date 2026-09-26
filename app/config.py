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
DB_PASS = os.environ.get("DB_PASS")
if not DB_PASS:
    raise ValueError("DB_PASS environment variable is missing!")

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

MAGIC_IV = 7 + 16                 # Magic (7) + IV (16), before each block's ciphertext

def get_encrypted_file_size(file_size: int) -> int:
    """Exact on-disk size of a NEOSYNC-encrypted file of `file_size` plaintext bytes.

    Each block is magic + IV + AES-CBC ciphertext. PKCS7 pads 1-16 bytes (a
    full 16 only when the block length is a multiple of 16), so only full
    blocks cost exactly OVERHEAD. Assuming 16 for the last block too left up
    to 15 stale bytes behind whenever a file shrank, and those blobs no longer
    decrypt (WRONG_FINAL_BLOCK_LENGTH on download).
    """
    if file_size <= 0:
        return 0
    bs = get_block_size(file_size)
    full, rem = divmod(file_size, bs)
    size = full * (bs + OVERHEAD)
    if rem:
        size += rem + MAGIC_IV + (16 - rem % 16)
    return size

# Backwards compatibility / defaults
BLOCK_SIZE = LARGE_BLOCK_SIZE
ENCRYPTED_BLOCK_SIZE = BLOCK_SIZE + OVERHEAD

# --- CORS ---
CORS_ORIGINS = os.environ.get("VAULTSYNC_CORS_ORIGINS", "*").split(",")

# --- RomM Integration ---
ROMM_URL = os.environ.get("ROMM_URL", "")
ROMM_API_KEY = os.environ.get("ROMM_API_KEY", "")
VAULTSYNC_VERSION = "1.0.0"
ROMM_DEVICE_NAME = "vaultsync"
ROMM_DEVICE_API_MIN_VERSION = "4.7.0"

ROMM_EMULATOR_MAP = {
    'switch': 'eden', 'eden': 'eden',
    'gc': 'dolphin', 'dolphin': 'dolphin', 'wii': 'dolphin',
    'psp': 'ppsspp', 'ppsspp': 'ppsspp',
    'ps2': 'pcsx2', 'pcsx2': 'pcsx2', 'aethersx2': 'pcsx2',
    'nethersx2': 'pcsx2', 'aethersx2-turnip': 'pcsx2',
    'nethersx2-turnip': 'pcsx2',
    'xyz.aethersx2.android': 'pcsx2', 'xyz.nethersx2.android': 'pcsx2',
    'xyz.aethersx2.custom': 'pcsx2', 'xyz.aethersx2.tturnip': 'pcsx2',
    '3ds': 'citra', 'citra': 'citra', 'azahar': 'citra',
    'retroarch': 'retroarch',
}

def romm_emulator_for(platform: str) -> str:
    if not platform:
        return 'retroarch'
    return ROMM_EMULATOR_MAP.get(platform.lower(), 'retroarch')

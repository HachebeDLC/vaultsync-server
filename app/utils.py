import os
import hashlib
import aiofiles
from typing import List
from .config import STORAGE_DIR, get_encrypted_block_size, get_block_size

async def calculate_file_hash_and_blocks(file_path: str) -> tuple[str, List[str]]:
    """Single-pass: calculates the full SHA-256 hash and block hashes."""
    full_sha256 = hashlib.sha256()
    block_hashes = []
    if not os.path.exists(file_path):
        return "", []
    
    file_size = os.path.getsize(file_path)
    
    # Auto-detect if file is encrypted (VaultSync Mode A) or plaintext (Mode B)
    # by checking the first 7 bytes for 'NEOSYNC'
    is_encrypted = False
    if file_size >= 7:
        async with aiofiles.open(file_path, "rb") as f:
            magic = await f.read(7)
            if magic == b"NEOSYNC":
                is_encrypted = True

    chunk_size = get_encrypted_block_size(file_size) if is_encrypted else get_block_size(file_size)
    
    async with aiofiles.open(file_path, "rb") as file:
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            full_sha256.update(chunk)
            block_hashes.append(hashlib.sha256(chunk).hexdigest())
    first_digest = full_sha256.digest()
    return hashlib.sha256(first_digest).hexdigest(), block_hashes

def is_safe_path(user_id: int, path: str) -> bool:
    """
    Prevents directory traversal attacks by verifying the path is within the user's root directory.
    Uses realpath() instead of abspath() to resolve symlinks before comparison.
    """
    user_root = os.path.realpath(os.path.join(STORAGE_DIR, str(user_id)))
    requested_path = os.path.realpath(os.path.join(user_root, path.lstrip("/\\")))
    return os.path.commonpath([user_root, requested_path]) == user_root

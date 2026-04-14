import sys
import os
import base64
import hashlib
import json
import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding, hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend

# --- PROTOCOL CONSTANTS ---
BLOCK_SIZE_PLAIN = 1024 * 1024
MAGIC = b"VAULTSYNC"
MAGIC_SIZE = len(MAGIC)
IV_SIZE = 16
PADDING_OVERHEAD = 16
# A full 1MiB block is 9 + 16 + 1,048,576 + 16 = 1,048,617
ENCRYPTED_BLOCK_SIZE = MAGIC_SIZE + IV_SIZE + BLOCK_SIZE_PLAIN + PADDING_OVERHEAD

def derive_master_key_pbkdf2(password, email):
    """New PBKDF2 derivation."""
    salt = email.encode('utf-8')
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
        backend=default_backend()
    )
    return kdf.derive(password.encode('utf-8'))

def derive_master_key_legacy(password, email):
    """Old SHA256 derivation."""
    bytes_in = f"{password}:{email}".encode('utf-8')
    return hashlib.sha256(bytes_in).digest()

def decrypt_block(block_data, key_bytes, use_padding=True):
    if len(block_data) < MAGIC_SIZE + IV_SIZE:
        return None
    
    magic = block_data[:MAGIC_SIZE]
    if magic != MAGIC:
        return None
        
    iv = block_data[MAGIC_SIZE : MAGIC_SIZE + IV_SIZE]
    ciphertext = block_data[MAGIC_SIZE + IV_SIZE:]
    
    cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    
    try:
        decrypted = decryptor.update(ciphertext) + decryptor.finalize()
        if use_padding:
            unpadder = padding.PKCS7(128).unpadder()
            return unpadder.update(decrypted) + unpadder.finalize()
        return decrypted
    except Exception:
        return None

def _login(base_url, email, password):
    """Authenticate and return a bearer token, or None on failure."""
    try:
        resp = requests.post(f"{base_url}/login", json={"email": email, "password": password})
        resp.raise_for_status()
        print("🔑 Login Successful")
        return resp.json()['token']
    except Exception as e:
        print(f"❌ Auth failed: {e}")
        return None


def _download(base_url, token, remote_path):
    """Download raw bytes for *remote_path*, or None on failure."""
    print(f"📥 Downloading {remote_path}...")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.post(f"{base_url}/api/v1/download", json={"filename": remote_path}, headers=headers)
        resp.raise_for_status()
        print(f"📦 Downloaded {len(resp.content)} bytes")
        return resp.content
    except Exception as e:
        print(f"❌ Download failed: {e}")
        return None


def _resolve_key(first_block, key_pbkdf2, key_legacy):
    """Return the active decryption key, or None if neither matches."""
    if decrypt_block(first_block, key_pbkdf2):
        print("✅ Key Match Found: PBKDF2 (New Standard)")
        return key_pbkdf2
    if decrypt_block(first_block, key_legacy):
        print("⚠️ Key Match Found: Legacy SHA-256 (Old Format)")
        return key_legacy
    print("❌ Error: Could not decrypt first block with either key.")
    print("   Possible causes: Wrong password/email, or file is corrupted.")
    raw_dec = decrypt_block(first_block, key_pbkdf2, use_padding=False)
    if raw_dec:
        print(f"   Diagnostic Peek (PBKDF2): {raw_dec[:32].hex()}...")
    return None


def _decrypt_all_blocks(raw_data, active_key):
    """Decrypt all blocks and return plaintext bytearray."""
    output_data = bytearray()
    offset = 0
    block_num = 0
    while offset < len(raw_data):
        remaining = len(raw_data) - offset
        if raw_data[offset : offset + MAGIC_SIZE] != MAGIC:
            print(f"❌ Critical: Magic mismatch at block {block_num} (offset {offset})")
            break
        current_block_size = min(ENCRYPTED_BLOCK_SIZE, remaining)
        plain = decrypt_block(raw_data[offset : offset + current_block_size], active_key)
        if plain is None:
            print(f"❌ Decryption failed at block {block_num}")
            break
        output_data.extend(plain)
        offset += current_block_size
        block_num += 1
    return output_data


def run_verify(base_url, email, password, remote_path):
    print(f"🚀 Connecting to VaultSync at {base_url}...")
    token = _login(base_url, email, password)
    if not token:
        return

    raw_data = _download(base_url, token, remote_path)
    if raw_data is None:
        return

    print("🕵️ Testing Key Derivations...")
    first_block = raw_data[:min(ENCRYPTED_BLOCK_SIZE, len(raw_data))]
    active_key = _resolve_key(
        first_block,
        derive_master_key_pbkdf2(password, email),
        derive_master_key_legacy(password, email),
    )
    if not active_key:
        return

    print("🔓 Decrypting file...")
    output_data = _decrypt_all_blocks(raw_data, active_key)

    output_filename = "pc_verified_" + os.path.basename(remote_path)
    with open(output_filename, "wb") as f:
        f.write(output_data)

    print(f"✅ VERIFICATION COMPLETE")
    print(f"📄 Saved to: {output_filename}")
    print(f"📊 Final Plain Size: {len(output_data)} bytes")
    print(f"🔒 SHA256: {hashlib.sha256(output_data).hexdigest()}")

if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("Usage: python3 verify_sync.py <base_url> <email> <password> <remote_path>")
    else:
        run_verify(sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3], sys.argv[4])

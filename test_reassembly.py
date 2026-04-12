import os
import hashlib
from app.services.reassembly_service import reassembly_service
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

def pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    padding_len = block_size - (len(data) % block_size)
    return data + bytes([padding_len] * padding_len)

def test_reassembly():
    key = b"0123456789abcdef0123456789abcdef" # 32 bytes for AES-256
    original_data = b"Hello, this is a test of the NeoSync reassembly service. " * 100
    original_size = len(original_data)
    
    # Mock an encrypted NeoSync file
    # We'll use 256KB blocks (small)
    block_size = 256 * 1024
    enc_file_path = "test_encrypted.bin"
    output_path = "test_decrypted.bin"
    
    with open(enc_file_path, "wb") as f:
        for i in range(0, original_size, block_size):
            chunk = original_data[i:i+block_size]
            iv = hashlib.md5(chunk).digest()
            
            cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
            encryptor = cipher.encryptor()
            padded_chunk = pkcs7_pad(chunk)
            encrypted_chunk = encryptor.update(padded_chunk) + encryptor.finalize()
            
            f.write(b"NEOSYNC")
            f.write(iv)
            f.write(encrypted_chunk)
            
    # Run reassembly
    try:
        reassembly_service.reassemble_file(enc_file_path, output_path, key, original_size)
        
        with open(output_path, "rb") as f:
            decrypted_data = f.read()
            
        if decrypted_data == original_data:
            print("SUCCESS: Reassembled data matches original!")
        else:
            print("FAILURE: Data mismatch!")
            print(f"Original size: {len(original_data)}")
            print(f"Decrypted size: {len(decrypted_data)}")
    finally:
        if os.path.exists(enc_file_path): os.remove(enc_file_path)
        if os.path.exists(output_path): os.remove(output_path)

if __name__ == "__main__":
    test_reassembly()

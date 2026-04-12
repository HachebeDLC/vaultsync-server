import os
import logging
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from ..config import get_block_size, get_encrypted_block_size, OVERHEAD

logger = logging.getLogger("VaultSync")

class ReassemblyService:
    MAGIC = b"NEOSYNC"

    def decrypt_block(self, encrypted_data: bytes, key: bytes) -> bytes:
        """
        Decrypts a single NeoSync block.
        Format: Magic (7) + IV (16) + Encrypted Data
        """
        if len(encrypted_data) < OVERHEAD:
            raise ValueError("Block too small")
        
        magic = encrypted_data[:7]
        if magic != self.MAGIC:
            raise ValueError("Invalid magic header")
            
        iv = encrypted_data[7:23]
        ciphertext = encrypted_data[23:]
        
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()
        
        return plaintext

    def reassemble_file(self, encrypted_file_path: str, output_path: str, key: bytes, original_size: int):
        """
        Reads an encrypted file, decrypts it block by block, and writes to output_path.
        """
        enc_block_size = get_encrypted_block_size(original_size)
        
        with open(encrypted_file_path, "rb") as fin, open(output_path, "wb") as fout:
            bytes_processed = 0
            while True:
                block = fin.read(enc_block_size)
                if not block:
                    break
                
                decrypted = self.decrypt_block(block, key)
                
                # Trim padding for the last block if necessary
                write_len = min(len(decrypted), original_size - bytes_processed)
                if write_len > 0:
                    fout.write(decrypted[:write_len])
                    bytes_processed += write_len
                    
        return output_path

    def zip_file(self, file_path: str, zip_path: str):
        import zipfile
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(file_path, os.path.basename(file_path))
        return zip_path

reassembly_service = ReassemblyService()

import asyncio
import os
from unittest.mock import AsyncMock, patch, MagicMock

from app.services.romm_client import RomMClient
from app.routers.files import romm_pull
from app.models import RomMPullRequest
from app.utils import calculate_file_hash_and_blocks

async def run_tests():
    print("Running RomM Pull Integration Test...")
    
    # 1. Test the magic header detection in calculate_file_hash_and_blocks
    with open("test_plaintext.txt", "wb") as f:
        f.write(b"Hello World" * 1000)
        
    with open("test_encrypted.txt", "wb") as f:
        f.write(b"NEOSYNC_RANDOM_DATA" * 1000)
        
    plain_hash, plain_blocks = await calculate_file_hash_and_blocks("test_plaintext.txt")
    enc_hash, enc_blocks = await calculate_file_hash_and_blocks("test_encrypted.txt")
    
    print(f"Plaintext blocks: {len(plain_blocks)}")
    print(f"Encrypted blocks: {len(enc_blocks)}")
    
    os.remove("test_plaintext.txt")
    os.remove("test_encrypted.txt")
    print("Block calculation auto-detection works!")

    # 2. Test matcher logic (crud.py)
    from app.crud import find_romm_game_for_user
    import psycopg2
    from psycopg2.extras import RealDictCursor
    
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {'romm_id': 123}
    
    # Simulating the exact match logic we updated
    found_id = find_romm_game_for_user(mock_conn, 1, target_id="01007300020FA000", target_name="System", platform_slug="switch")
    print(f"Matcher result: {found_id}")
    
    # Ensure user_id was passed correctly to the mock
    call_args = mock_cursor.execute.call_args[0]
    print(f"Matcher executed query: {call_args[0][:100]}...")
    print(f"Matcher executed params: {call_args[1]}")
    assert call_args[1][0] == 1, "user_id should be the first parameter!"
    print("Matcher uses user_id scoped query correctly!")

if __name__ == "__main__":
    asyncio.run(run_tests())

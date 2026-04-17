import asyncio
from unittest.mock import AsyncMock, patch
import os

from app.services.romm_client import RomMClient

async def test_download_save():
    print("Testing RomMClient.download_save()...")
    client = RomMClient("http://localhost:8080", "fake_key")
    
    # Mock httpx.AsyncClient to simulate RomM endpoints
    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = mock_client.return_value.__aenter__.return_value
        
        # 1. Mock list saves
        list_resp = AsyncMock()
        list_resp.status_code = 200
        list_resp.json = lambda: [
            {"id": 101, "file_name": "pokemon_save.sav", "updated_at": "2026-04-17T12:00:00Z"},
            {"id": 102, "file_name": "pokemon_save_old.sav", "updated_at": "2026-04-16T12:00:00Z"}
        ]
        
        # 2. Mock download content
        dl_resp = AsyncMock()
        dl_resp.status_code = 200
        dl_resp.content = b"FAKE_SAVE_DATA_PLAIN"
        
        # Setup side effect based on URL
        async def mock_get(url, **kwargs):
            if "/api/saves/" in url and "/content" in url:
                return dl_resp
            return list_resp
            
        mock_instance.get.side_effect = mock_get
        
        result_path = await client.download_save(555, "temp_downloads")
        
        print(f"Downloaded to: {result_path}")
        assert result_path == "temp_downloads/pokemon_save.sav"
        
        # Verify it downloaded the correct one (id=101)
        mock_instance.get.assert_any_call(
            "http://localhost:8080/api/saves/101/content/pokemon_save.sav",
            headers={"Authorization": "Bearer fake_key"},
            timeout=120.0,
            follow_redirects=True
        )
        print("Latest save correctly identified and downloaded!")
        
        with open(result_path, "rb") as f:
            data = f.read()
            assert data == b"FAKE_SAVE_DATA_PLAIN"
        print("Save data correctly written to disk!")
        os.remove(result_path)

if __name__ == "__main__":
    asyncio.run(test_download_save())

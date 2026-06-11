import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
import os

from app.services.romm_client import RomMClient

async def test_download_save():
    print("Testing RomMClient.download_save()...")
    client = RomMClient("http://localhost:8080", "fake_key")

    # Mock httpx.AsyncClient (persistent-client pattern + streamed download)
    with patch("app.services.romm_client.httpx.AsyncClient") as mock_client:
        mock_instance = mock_client.return_value
        mock_instance.is_closed = False

        # 1. Mock list saves (plain GET)
        list_resp = AsyncMock()
        list_resp.status_code = 200
        list_resp.json = lambda: [
            {"id": 101, "file_name": "pokemon_save.sav", "updated_at": "2026-04-17T12:00:00Z"},
            {"id": 102, "file_name": "pokemon_save_old.sav", "updated_at": "2026-04-16T12:00:00Z"}
        ]
        mock_instance.get = AsyncMock(return_value=list_resp)

        # 2. Mock download content (client.stream context manager)
        stream_urls = []

        @asynccontextmanager
        async def mock_stream(method, url, **kwargs):
            stream_urls.append(url)
            resp = MagicMock()
            resp.status_code = 200

            async def aiter_bytes(chunk_size=65536):
                yield b"FAKE_SAVE_DATA_PLAIN"

            resp.aiter_bytes = aiter_bytes
            yield resp

        mock_instance.stream.side_effect = lambda *a, **kw: mock_stream(*a, **kw)

        result_path = await client.download_save(555, "temp_downloads")

        print(f"Downloaded to: {result_path}")
        assert result_path == "temp_downloads/pokemon_save.sav"

        # Verify it downloaded the correct one (id=101, the latest)
        assert stream_urls == ["http://localhost:8080/api/saves/101/content/pokemon_save.sav"]
        print("Latest save correctly identified and downloaded!")

        with open(result_path, "rb") as f:
            data = f.read()
            assert data == b"FAKE_SAVE_DATA_PLAIN"
        print("Save data correctly written to disk!")
        os.remove(result_path)

if __name__ == "__main__":
    asyncio.run(test_download_save())

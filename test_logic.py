import sys
import os
import unittest
from unittest.mock import patch, MagicMock

# Setup path
sys.path.append(os.path.dirname(__file__))

# Mock config
os.environ["VAULTSYNC_SECRET"] = "test_secret"
os.environ["DATABASE_URL"] = "postgresql://user:pass@localhost/db"
os.environ["ROMM_URL"] = "http://mock-romm"
os.environ["ROMM_API_KEY"] = "mock-key"

from app.services.title_db_service import TitleDBService
from app.services.romm_client import RomMClient

class TestVaultSyncLogic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assets_path = os.path.join(os.path.dirname(__file__), "app", "assets")
        cls.title_db = TitleDBService(assets_path)

    def test_3ds_translation(self):
        name = self.title_db.translate("00030700")
        self.assertEqual(name, "Mario Kart™ 7")

    def test_smart_link_translation_flow(self):
        # 01007300020FA000 is Zelda BOTW in title_db.json
        client = RomMClient()
        with patch("httpx.AsyncClient.get") as mock_get:
            # Mock RomM returning a list of games for the 'switch' platform
            mock_get.return_value = MagicMock(
                status_code=200, 
                json=lambda: {
                    "items": [
                        {"id": 456, "name": "The Legend of Zelda: Breath of the Wild", "fs_name": "Zelda.nsp"}
                    ]
                }
            )
            
            import asyncio
            rom_id = asyncio.run(client.get_rom_id_by_path("switch/01007300020FA000/system.sav"))
            
            # Check ID was found via translated name match
            self.assertEqual(rom_id, 456)
            
            # Verify it used platform_slug to avoid the RomM 500 bug
            args, kwargs = mock_get.call_args
            params = kwargs.get("params", {})
            self.assertEqual(params.get("platform_slug"), "switch")

    def test_leading_number_stripping(self):
        client = RomMClient()
        with patch("httpx.AsyncClient.get") as mock_get:
            # "67. Dragon Ball Z" in RomM
            mock_get.return_value = MagicMock(
                status_code=200, 
                json=lambda: {
                    "items": [
                        {"id": 138, "name": "2 Games in 1 - Dragon Ball Z - The Legacy of Goku I & II", "fs_name": "67. Dragon Ball Z.gba"}
                    ]
                }
            )
            
            import asyncio
            rom_id = asyncio.run(client.get_rom_id_by_path("RetroArch/saves/mGBA/67. Dragon Ball Z.srm"))
            
            self.assertEqual(rom_id, 138)

if __name__ == "__main__":
    unittest.main()

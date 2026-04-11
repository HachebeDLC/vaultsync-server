import os
import logging
import httpx
from typing import Optional, Dict, Any
from ..config import ROMM_URL, ROMM_API_KEY

logger = logging.getLogger("VaultSync")

class RomMClient:
    def __init__(self, base_url: str = ROMM_URL, api_key: str = ROMM_API_KEY):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}" if api_key else ""
        }

    async def get_rom_id_by_path(self, path: str) -> Optional[int]:
        """
        Attempts to find a ROM in RomM by its relative path.
        Example: 'switch/01007300020FA000'
        """
        if not self.api_key:
            return None
            
        try:
            # Note: RomM API might need specific search params. 
            # We'll search by name (basename of path) and filter by platform.
            parts = path.split("/")
            platform = parts[0]
            name = parts[-1]
            
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base_url}/api/roms",
                    params={"search_term": name, "platform_slug": platform},
                    headers=self.headers
                )
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("results", [])
                    if results:
                        # Return the first match
                        return results[0].get("id")
        except Exception as e:
            logger.error(f"RomM look up failed: {str(e)}")
        return None

    async def upload_save(self, rom_id: int, file_path: str, device_id: str = "NeoSync"):
        """
        Uploads a reassembled save file to RomM.
        """
        if not self.api_key:
            return False

        try:
            async with httpx.AsyncClient() as client:
                with open(file_path, "rb") as f:
                    files = {"saveFile": (os.path.basename(file_path), f, "application/zip")}
                    resp = await client.post(
                        f"{self.base_url}/api/saves",
                        params={
                            "rom_id": rom_id,
                            "device_id": device_id,
                            "overwrite": "true"
                        },
                        files=files,
                        headers=self.headers,
                        timeout=60.0
                    )
                    if resp.status_code in (200, 201):
                        logger.info(f"Successfully uploaded save to RomM for ROM {rom_id}")
                        return True
                    else:
                        logger.error(f"RomM upload failed: {resp.status_code} - {resp.text}")
        except Exception as e:
            logger.error(f"RomM upload error: {str(e)}")
        return False

romm_client = RomMClient()

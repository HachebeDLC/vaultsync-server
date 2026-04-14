import os
import httpx
import logging
import json
import re
from typing import Optional, Dict, List
from ..config import ROMM_URL, ROMM_API_KEY

logger = logging.getLogger("VaultSync")

class RomMClient:
    def __init__(self, base_url: str = ROMM_URL, api_key: str = ROMM_API_KEY):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {"Authorization": f"Bearer {api_key}"}

    async def fetch_entire_library(self) -> List[Dict]:
        """Fetches the user's entire ROM library from RomM to cache locally."""
        if not self.api_key:
            return []
            
        all_items = []
        offset = 0
        limit = 1000
        
        async with httpx.AsyncClient() as client:
            while True:
                try:
                    resp = await client.get(
                        f"{self.base_url}/api/roms", 
                        params={"limit": limit, "offset": offset},
                        headers=self.headers, 
                        timeout=60.0
                    )
                    
                    if resp.status_code == 200:
                        data = resp.json()
                        items = data.get("items", [])
                        if not items:
                            break
                            
                        all_items.extend(items)
                        offset += limit
                        
                        # Optimization: if the server returned fewer than the limit, we're done.
                        if len(items) < limit:
                            break
                    else:
                        logger.error(f"RomM API Error: {resp.status_code} to {self.base_url}")
                        break
                        
                except Exception as e:
                    logger.error(f"Failed to fetch RomM library: {str(e)}")
                    break
                    
        return all_items

    async def upload_save(self, rom_id: int, file_path: str, device_id: str = "NeoSync"):
        if not self.api_key:
            return False

        try:
            filename = os.path.basename(file_path)
            params = {"rom_id": rom_id, "device_id": device_id, "overwrite": "true"}
            if ".state" in filename.lower():
                params["slot"] = "state_auto" if "auto" in filename.lower() else "state_manual"

            async with httpx.AsyncClient() as client:
                with open(file_path, "rb") as f:
                    files = {"saveFile": (filename, f, "application/octet-stream")}
                    resp = await client.post(f"{self.base_url}/api/saves", params=params, files=files, headers=self.headers, timeout=60.0)
                    return resp.status_code in (200, 201)
        except Exception as e:
            logger.error(f"RomM upload failed: {str(e)}")
        return False

# Global default instance
romm_client = RomMClient()

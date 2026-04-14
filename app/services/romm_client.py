import os
import httpx
import logging
import asyncio
import json
import re
import time
from typing import Optional, Dict, List
from ..config import ROMM_URL, ROMM_API_KEY

logger = logging.getLogger("VaultSync")

class RomMClient:
    def __init__(self, base_url: str = ROMM_URL, api_key: str = ROMM_API_KEY):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {"Authorization": f"Bearer {api_key}"}
        # Simple TTL Cache for platform lists to prevent DDOSing RomM
        self._platform_cache: Dict[str, Dict] = {}
        self._cache_ttl = 300 # 5 minutes

    def _get_from_cache(self, platform_slug: str) -> Optional[List[Dict]]:
        if platform_slug in self._platform_cache:
            entry = self._platform_cache[platform_slug]
            if time.time() - entry['timestamp'] < self._cache_ttl:
                return entry['data']
        return None

    def _save_to_cache(self, platform_slug: str, data: List[Dict]):
        self._platform_cache[platform_slug] = {
            'timestamp': time.time(),
            'data': data
        }

    async def get_rom_id_by_path(self, path: str) -> Optional[int]:
        if not self.api_key:
            return None
            
        try:
            from .title_db_service import title_db
            
            # Load manual overrides
            overrides_path = os.path.join(os.path.dirname(__file__), '..', 'title_db.json')
            overrides = {}
            if os.path.exists(overrides_path):
                with open(overrides_path, 'r') as f:
                    overrides = json.load(f)

            parts = path.split("/")
            platform = parts[0].lower()
            
            platform_map = {
                'switch': 'switch', 'eden': 'switch',
                'gc': 'gamecube', 'dolphin': 'gamecube', 'wii': 'wii',
                'psp': 'psp', 'ppsspp': 'psp',
                'ps2': 'ps2', 'pcsx2': 'ps2', 'aethersx2': 'ps2',
                '3ds': '3ds', 'citra': '3ds', 'azahar': '3ds',
                'gba': 'gba', 'snes': 'snes', 'n64': 'n64', 'nds': 'nds',
                'gb': 'gb', 'gbc': 'gbc', 'nes': 'nes', 'megadrive': 'megadrive'
            }
            
            romm_platform = platform_map.get(platform)
            target_id = None
            target_name = parts[-1]
            
            if platform in ('switch', 'eden') and len(parts) >= 3:
                target_id = parts[1].upper()
            elif platform in ('gc', 'dolphin', 'wii') and len(parts) >= 2:
                target_id = parts[1].split('.')[0].upper()
            elif platform in ('psp', 'ppsspp') and len(parts) >= 3 and parts[1] == 'SAVEDATA':
                target_id = parts[2].upper()
            elif platform == '3ds' and len(parts) >= 3 and parts[1] == 'saves':
                target_id = parts[2].upper()

            translated_name = None
            if target_id and target_id in overrides:
                translated_name = overrides[target_id]
            if not translated_name and target_id:
                translated_name = title_db.translate(target_id)

            if translated_name:
                target_name = translated_name

            clean_target = os.path.splitext(target_name)[0]
            clean_target = re.sub(r'^\d+\.\s*', '', clean_target).lower().strip()

            # 1. CHECK CACHE FIRST
            items = None
            if romm_platform:
                items = self._get_from_cache(romm_platform)

            # 2. FETCH IF NOT CACHED
            if items is None:
                async with httpx.AsyncClient() as client:
                    params = {"limit": 1000}
                    if romm_platform:
                        params["platform_slug"] = romm_platform
                    else:
                        params["search_term"] = clean_target

                    resp = await client.get(f"{self.base_url}/api/roms", params=params, headers=self.headers, timeout=30.0)
                    if resp.status_code == 200:
                        data = resp.json()
                        items = data.get("items", [])
                        if romm_platform:
                            self._save_to_cache(romm_platform, items)
                            logger.info(f"RomM Cache: Saved {len(items)} items for {romm_platform}")
                    else:
                        logger.error(f"RomM API Error: {resp.status_code} to {self.base_url}")
                        return None

            # 3. LOCAL FILTERING
            if items:
                for rom in items:
                    rom_str = str(rom).upper()
                    rom_name = rom.get("name", "").lower()
                    rom_fs_name = rom.get("fs_name", "").lower()
                    
                    if target_id and not translated_name and target_id in rom_str:
                        return rom['id']
                    
                    clean_rom_name = re.sub(r'^\d+\.\s*', '', rom_name).strip()
                    clean_rom_fs = re.sub(r'^\d+\.\s*', '', rom_fs_name).strip()
                    
                    if clean_target in clean_rom_name or clean_rom_name in clean_target:
                         return rom['id']
                    if clean_target in clean_rom_fs or clean_rom_fs in clean_target:
                         return rom['id']
                             
        except Exception as e:
            logger.error(f"RomM Smart-Link failed: {str(e)}")
        return None

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

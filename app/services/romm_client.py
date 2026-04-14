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
        Hyper-robust lookup: 
        1. Manual Overrides (title_db.json)
        2. Asset DBs (GameDB TSVs/JSONs)
        3. Smart Fuzzy Match (RomM Search)
        """
        if not self.api_key:
            return None
            
        try:
            import re
            import json
            from .title_db_service import title_db
            
            # Load manual overrides
            overrides_path = os.path.join(os.path.dirname(__file__), '..', 'title_db.json')
            overrides = {}
            if os.path.exists(overrides_path):
                with open(overrides_path, 'r') as f:
                    overrides = json.load(f)

            parts = path.split("/")
            platform = parts[0].lower()
            
            # Map VaultSync platforms to RomM platform slugs
            platform_map = {
                'switch': 'switch', 'eden': 'switch',
                'gc': 'gamecube', 'dolphin': 'gamecube', 'wii': 'wii',
                'psp': 'psp', 'ppsspp': 'psp',
                'ps2': 'ps2', 'pcsx2': 'ps2', 'aethersx2': 'ps2',
                '3ds': '3ds', 'citra': '3ds', 'azahar': '3ds',
                'gba': 'gba', 'snes': 'snes', 'n64': 'n64', 'nds': 'nds'
            }
            
            romm_platform = platform_map.get(platform)
            target_id = None
            target_name = parts[-1]
            
            # 1. Extract the internal ID (TitleID/GameID/Serial)
            if platform in ('switch', 'eden') and len(parts) >= 3:
                target_id = parts[1].upper() # 01007300020FA000
            elif platform in ('gc', 'dolphin', 'wii') and len(parts) >= 2:
                target_id = parts[1].split('.')[0].upper() # GZLE01
            elif platform in ('psp', 'ppsspp') and len(parts) >= 3 and parts[1] == 'SAVEDATA':
                target_id = parts[2].upper() # ULUS10025
            elif platform == '3ds' and len(parts) >= 3 and parts[1] == 'saves':
                target_id = parts[2].upper() # 00030700

            # 2. TRANSLATION LAYER
            translated_name = None
            
            # A. Check manual overrides first
            if target_id and target_id in overrides:
                translated_name = overrides[target_id]
                logger.info(f"RomM Link: Override Match {target_id} -> '{translated_name}'")
            
            # B. Check Asset DBs (GameDB)
            if not translated_name and target_id:
                translated_name = title_db.translate(target_id)
                if translated_name:
                    logger.info(f"RomM Link: AssetDB Match {target_id} -> '{translated_name}'")

            # Update search term if translated
            if translated_name:
                target_name = translated_name

            # 3. Clean search term for RomM API
            clean_target = os.path.splitext(target_name)[0]
            clean_target = re.sub(r'^\d+\.\s*', '', clean_target).lower().strip()

            logger.info(f"RomM Link: Platform='{romm_platform or platform}', ID='{target_id}', Search='{clean_target}'")

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
                    
                    if not items:
                        return None
                        
                    for rom in items:
                        rom_str = str(rom).upper()
                        rom_name = rom.get("name", "").lower()
                        rom_fs_name = rom.get("fs_name", "").lower()
                        
                        # Direct ID match (only if not already translated)
                        if target_id and not translated_name and target_id in rom_str:
                            logger.info(f"RomM Link: Found ID {target_id} in RomM Metadata: {rom_name}")
                            return rom['id']
                            
                        # Name/FS match
                        clean_rom_name = re.sub(r'^\d+\.\s*', '', rom_name).strip()
                        clean_rom_fs = re.sub(r'^\d+\.\s*', '', rom_fs_name).strip()
                        
                        if clean_target in clean_rom_name or clean_rom_name in clean_target:
                             logger.info(f"RomM Link: Found Name Match: {rom_name} (ID: {rom['id']})")
                             return rom['id']
                        if clean_target in clean_rom_fs or clean_rom_fs in clean_target:
                             logger.info(f"RomM Link: Found FS Match: {rom_fs_name} (ID: {rom['id']})")
                             return rom['id']
                             
            logger.warning(f"RomM Link: No match found for '{target_id or clean_target}'")
        except Exception as e:
            logger.error(f"RomM Link failed for {path}: {str(e)}")
        return None
    async def upload_save(self, rom_id: int, file_path: str, device_id: str = "NeoSync"):
        """
        Uploads a raw decrypted save file directly to RomM.
        """
        if not self.api_key:
            return False

        try:
            filename = os.path.basename(file_path)
            
            # Determine if it's a save state to use RomM's slot system
            params = {
                "rom_id": rom_id,
                "device_id": device_id,
                "overwrite": "true"
            }
            
            if ".state" in filename.lower():
                params["slot"] = "state_auto" if "auto" in filename.lower() else "state_manual"

            async with httpx.AsyncClient() as client:
                with open(file_path, "rb") as f:
                    # Send the raw file, not a zip!
                    files = {"saveFile": (filename, f, "application/octet-stream")}
                    resp = await client.post(
                        f"{self.base_url}/api/saves",
                        params=params,
                        files=files,
                        headers=self.headers,
                        timeout=60.0
                    )
                    if resp.status_code in (200, 201):
                        logger.info(f"Successfully uploaded {filename} to RomM for ROM {rom_id}")
                        return True
                    else:
                        logger.error(f"RomM upload failed: {resp.status_code} - {resp.text}")
        except Exception as e:
            logger.error(f"RomM upload error: {str(e)}")
        return False

# Global default instance (uses env vars)
romm_client = RomMClient()

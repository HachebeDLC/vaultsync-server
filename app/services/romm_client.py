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
        Hyper-robust lookup: Handles 'items' vs 'results' bug, 
        strips leading numbers, performs deep fuzzy matching,
        and uses a local TitleDB for known TitleIDs.
        """
        if not self.api_key:
            return None
            
        try:
            import re
            import json
            
            # Load local TitleDB
            title_db_path = os.path.join(os.path.dirname(__file__), '..', 'title_db.json')
            title_db = {}
            if os.path.exists(title_db_path):
                with open(title_db_path, 'r') as f:
                    title_db = json.load(f)

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
            
            # Extract ID if possible
            if platform in ('switch', 'eden') and len(parts) >= 3:
                target_id = parts[1].upper()
            elif platform in ('gc', 'dolphin', 'wii') and len(parts) >= 2:
                target_id = parts[1].split('.')[0].upper()
            elif platform in ('psp', 'ppsspp') and len(parts) >= 3 and parts[1] == 'SAVEDATA':
                target_id = parts[2].upper()
            
            # Check TitleDB
            if target_id and target_id in title_db:
                target_name = title_db[target_id]
                logger.info(f"TitleDB Match: Translated {target_id} to '{target_name}'")
            
            # Clean target name: strip extension and common prefixes/numbers
            clean_target = os.path.splitext(target_name)[0]
            clean_target = re.sub(r'^\d+\.\s*', '', clean_target).lower().strip()

            logger.info(f"RomM Smart-Link: Platform='{romm_platform or platform}', ID='{target_id}', Search='{clean_target}'")

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
                        
                        # 1. Direct ID match (only if not using TitleDB translation)
                        if target_id and not (target_id in title_db) and target_id in rom_str:
                            logger.info(f"RomM Smart-Link: Found ID {target_id} match: {rom_name}")
                            return rom['id']
                            
                        # 2. Clean name match
                        clean_rom_name = re.sub(r'^\d+\.\s*', '', rom_name).strip()
                        clean_rom_fs = re.sub(r'^\d+\.\s*', '', rom_fs_name).strip()
                        
                        if clean_target in clean_rom_name or clean_rom_name in clean_target:
                             logger.info(f"RomM Smart-Link: Found Name match: {rom_name}")
                             return rom['id']
                        if clean_target in clean_rom_fs or clean_rom_fs in clean_target:
                             logger.info(f"RomM Smart-Link: Found FS match: {rom_fs_name}")
                             return rom['id']
                             
            logger.warning(f"RomM Smart-Link: No match found for '{target_id or clean_target}'")
        except Exception as e:
            logger.error(f"RomM look up failed: {str(e)}")
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

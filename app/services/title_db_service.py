import os
import json
import csv
import logging
import re
from typing import Optional, Dict

logger = logging.getLogger("VaultSync")

class TitleDBService:
    def __init__(self, assets_dir: str):
        self.assets_dir = assets_dir
        self.db: Dict[str, str] = {}
        self._load_all()

    def _load_all(self):
        """Loads all supported databases from the assets directory."""
        if not os.path.exists(self.assets_dir):
            logger.warning(f"Assets directory not found: {self.assets_dir}")
            return

        for filename in os.listdir(self.assets_dir):
            path = os.path.join(self.assets_dir, filename)
            if filename.endswith(".tsv"):
                self._load_tsv(path)
            elif filename.endswith(".json"):
                self._load_json(path)
        
        logger.info(f"TitleDB: Loaded {len(self.db)} mappings from assets.")

    def _load_tsv(self, path: str):
        """Parses GameDB style TSV files."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f, delimiter='\t')
                for row in reader:
                    # Map both ID and Serial to the Title
                    title = row.get('title') or row.get('name')
                    if not title: continue
                    
                    # Store by ID
                    if 'ID' in row:
                        self.db[row['ID'].upper()] = title
                    # Store by Serial (often different)
                    if 'serial' in row:
                        self.db[row['serial'].upper()] = title
        except Exception as e:
            logger.error(f"Failed to load TSV {path}: {str(e)}")

    def _load_json(self, path: str):
        """Parses 3DS eShop style JSON files."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
                # Format 1: [{Name: "...", TitleID: "..."}] (hax0kartik/3dsdb)
                if isinstance(data, list):
                    for item in data:
                        name = item.get('Name') or item.get('name')
                        tid = item.get('TitleID') or item.get('titleId')
                        if name and tid:
                            self.db[str(tid).upper()] = name
                            
                # Format 2: Nested regions (the other one)
                elif isinstance(data, dict):
                    for region in data.values():
                        if isinstance(region, list):
                            for item in region:
                                name = item.get('name')
                                tid = item.get('id')
                                if name and tid:
                                    self.db[str(tid).upper()] = name
        except Exception as e:
            logger.error(f"Failed to load JSON {path}: {str(e)}")
    def translate(self, identifier: str) -> Optional[str]:
        """Translates a TitleID or Serial to a Game Name."""
        if not identifier: return None
        
        clean_id = identifier.upper().strip()
        
        # 1. Direct match
        if clean_id in self.db:
            return self.db[clean_id]
        
        # 2. 3DS Low-ID matching (e.g. 00030700)
        # Often TitleIDs in DBs are full 16-char IDs, but folders are only 8-char.
        if len(clean_id) == 8:
            for db_id, name in self.db.items():
                if db_id.endswith(clean_id):
                    return name

        return None

# Singleton instance
assets_path = os.path.join(os.path.dirname(__file__), "..", "assets")
title_db = TitleDBService(assets_path)

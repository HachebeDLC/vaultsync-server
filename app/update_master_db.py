import urllib.request
import json
import os

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
MASTER_DB_PATH = os.path.join(ASSETS_DIR, "master_title_db.json")

def download_switch_db():
    url = "https://raw.githubusercontent.com/blawar/titledb/master/US.en.json"
    print(f"Downloading Switch TitleDB from {url}...")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode('utf-8'))
            mapping = {}
            for item in data.values():
                title_id = item.get("id")
                name = item.get("name")
                if title_id and name:
                    mapping[title_id.upper()] = name.strip()
            print(f"✅ Extracted {len(mapping)} Switch titles.")
            return mapping
    except Exception as e:
        print(f"❌ Failed to download Switch DB: {e}")
        return {}

def download_wii_gc_db():
    url = "https://www.gametdb.com/wiitdb.txt"
    print(f"Downloading Wii/GameCube GameTDB from {url}...")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            lines = response.read().decode('utf-8').splitlines()
            mapping = {}
            for line in lines:
                if "=" in line:
                    parts = line.split("=", 1)
                    title_id = parts[0].strip()
                    name = parts[1].strip()
                    # GameTDB IDs are 4 to 6 chars
                    if len(title_id) in (4, 6):
                        mapping[title_id.upper()] = name
            print(f"✅ Extracted {len(mapping)} Wii/GameCube titles.")
            return mapping
    except Exception as e:
        print(f"❌ Failed to download Wii/GC DB: {e}")
        return {}

def main():
    if not os.path.exists(ASSETS_DIR):
        os.makedirs(ASSETS_DIR)
        
    master_mapping = {}
    
    # Fetch Switch
    switch_map = download_switch_db()
    master_mapping.update(switch_map)
    
    # Fetch Wii/GC
    wii_gc_map = download_wii_gc_db()
    master_mapping.update(wii_gc_map)
    
    # Save to assets
    if master_mapping:
        with open(MASTER_DB_PATH, "w", encoding="utf-8") as f:
            json.dump(master_mapping, f, indent=2)
        print(f"\n🎉 Successfully created {MASTER_DB_PATH} with {len(master_mapping)} total titles!")
    else:
        print("\n❌ Failed to create Master DB. No data downloaded.")

if __name__ == "__main__":
    main()

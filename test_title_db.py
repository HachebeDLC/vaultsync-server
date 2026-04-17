import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

from services.title_db_service import title_db

def test_translation(identifier, expected_platform):
    name = title_db.translate(identifier)
    print(f"[{expected_platform}] {identifier} -> {name or 'NOT FOUND'}")

if __name__ == "__main__":
    print("--- TitleDB Translation Engine Test ---\n")
    test_translation("01007300020FA000", "Switch")
    test_translation("0100F2C0115B6000", "Switch")
    test_translation("ULUS10025", "PSP")
    test_translation("00030700", "3DS")
    test_translation("00033c00", "3DS")
    test_translation("GZLE01", "GameCube")
    test_translation("0100276009872000", "Switch")
    test_translation("0100535012974000", "Switch")

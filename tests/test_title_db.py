import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.title_db_service import title_db

SAMPLES = [
    ("01007300020FA000", "Switch"),
    ("0100F2C0115B6000", "Switch"),
    ("ULUS10025", "PSP"),
    ("00030700", "3DS"),
    ("00033c00", "3DS"),
    ("GZLE01", "GameCube"),
    ("0100276009872000", "Switch"),
    ("0100535012974000", "Switch"),
]


def check_translation(identifier, expected_platform):
    name = title_db.translate(identifier)
    print(f"[{expected_platform}] {identifier} -> {name or 'NOT FOUND'}")
    return name


def test_translate_smoke():
    """translate() must return str-or-None without raising for all sample IDs."""
    for identifier, platform in SAMPLES:
        name = check_translation(identifier, platform)
        assert name is None or isinstance(name, str)


if __name__ == "__main__":
    print("--- TitleDB Translation Engine Test ---\n")
    for identifier, platform in SAMPLES:
        check_translation(identifier, platform)

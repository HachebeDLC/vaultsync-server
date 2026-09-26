"""Exact encrypted size, the finalize truncation that uses it, and the repair
script for blobs damaged before it existed.

Production evidence (2026-09-26): a save shrank from 76825 to 76447 bytes;
finalize truncated the blob to 76447 + 39 = 76486 bytes, but the real
ciphertext ends at 76471 (PKCS7 added 1 byte, not 16). The 15 stale bytes made
every later download fail with WRONG_FINAL_BLOCK_LENGTH.
"""
import os
from contextlib import contextmanager
from unittest.mock import MagicMock

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app import database as _db  # noqa: E402
_db.get_pool = lambda: MagicMock()
_db.init_db = lambda: None

from fastapi.testclient import TestClient  # noqa: E402

from app import config, repair_stale_tails
from app import utils as utils_module
from app.dependencies import get_current_user
from app.main import app
from app.routers import files as files_router
from app.services.version_manager import version_manager

KEY = b"k" * 32


def encrypt_like_client(plain: bytes) -> bytes:
    """NEOSYNC layout the clients write: per block, magic + IV + AES-CBC/PKCS7."""
    if not plain:
        return b""
    bs = config.get_block_size(len(plain))
    out = b""
    for i in range(0, len(plain), bs):
        chunk = plain[i:i + bs]
        iv = os.urandom(16)
        padder = padding.PKCS7(128).padder()
        padded = padder.update(chunk) + padder.finalize()
        enc = Cipher(algorithms.AES(KEY), modes.CBC(iv)).encryptor()
        out += b"NEOSYNC" + iv + enc.update(padded) + enc.finalize()
    return out


def test_exact_size_matches_real_encryption():
    for n in (1, 15, 16, 17, 31, 32, 4012, 76447, 76825, 262143, 262144, 262145, 600000):
        assert config.get_encrypted_file_size(n) == len(encrypt_like_client(b"x" * n)), n


def test_exact_size_for_large_block_files():
    n = config.BLOCK_THRESHOLD + 12345
    assert config.get_encrypted_file_size(n) == len(encrypt_like_client(b"y" * n))


# --- finalize truncation ----------------------------------------------------

app.dependency_overrides[get_current_user] = lambda: {"id": 1, "email": "t@x"}
client = TestClient(app)


@contextmanager
def _fake_get_db():
    yield MagicMock()


def test_finalize_after_shrink_leaves_a_decryptable_blob(tmp_path, monkeypatch):
    monkeypatch.setattr(files_router, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(utils_module, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(version_manager, "storage_root", str(tmp_path))
    rel = "switch/010035F022078000/MainGameSaveData"
    blob = tmp_path / "1" / rel
    blob.parent.mkdir(parents=True)

    old, new = b"O" * 76825, b"N" * 76447
    blob.write_bytes(encrypt_like_client(old))
    # upload_fragment writes the new ciphertext at offset 0 without truncating
    new_enc = encrypt_like_client(new)
    with open(blob, "r+b") as f:
        f.write(new_enc)

    monkeypatch.setattr(files_router, "get_db", _fake_get_db)
    monkeypatch.setattr(files_router.crud, "get_file_metadata", lambda c, u, p: {"size": len(old), "device_name": "x"})
    monkeypatch.setattr(files_router.crud, "upsert_file_metadata", lambda *a, **k: None)

    resp = client.post("/api/v1/upload/finalize", json={
        "path": rel, "hash": "h", "size": len(new), "updated_at": 2, "device_name": "Nova"})
    assert resp.status_code == 200, resp.text
    assert blob.read_bytes() == new_enc, "blob must end exactly where the new ciphertext ends"


# --- repair script ----------------------------------------------------------

def _setup_repair(tmp_path, monkeypatch):
    monkeypatch.setattr(repair_stale_tails, "STORAGE_DIR", str(tmp_path))
    user = tmp_path / "1"
    (user / ".versions").mkdir(parents=True)
    return user


def test_repair_reports_then_trims_current_and_version_blobs(tmp_path, monkeypatch):
    user = _setup_repair(tmp_path, monkeypatch)
    good = encrypt_like_client(b"G" * 76447)
    (user / "switch").mkdir()
    (user / "switch" / "save").write_bytes(good + b"\xAA" * 15)
    (user / ".versions" / "switch_save.~20260926~Nova~").write_bytes(good + b"\xBB" * 15)
    (user / ".versions" / "clean.~1~X~").write_bytes(good)
    rows = [(1, "switch/save", 76447)]

    report = repair_stale_tails.run(apply=False, rows=rows)
    assert report["counts"][("current", "tail")] == 1
    assert report["counts"][("version", "tail")] == 1
    assert report["counts"][("version", "ok")] == 1
    assert (user / "switch" / "save").read_bytes() == good + b"\xAA" * 15, "dry run must not modify"

    repair_stale_tails.run(apply=True, rows=rows)
    assert (user / "switch" / "save").read_bytes() == good
    assert (user / ".versions" / "switch_save.~20260926~Nova~").read_bytes() == good


def test_repair_leaves_plaintext_short_and_malformed_blobs_alone(tmp_path, monkeypatch):
    user = _setup_repair(tmp_path, monkeypatch)
    (user / "plain.srm").write_bytes(b"not encrypted" * 10)
    good = encrypt_like_client(b"S" * 1000)
    (user / "short").write_bytes(good[:-5])
    (user / ".versions" / "garbled.~1~X~").write_bytes(b"NEOSYNC" + b"\x00" * 60 + b"junk")
    rows = [(1, "plain.srm", 130), (1, "short", 1000)]
    before = {p.name: p.read_bytes() for p in list(user.iterdir()) + list((user / ".versions").iterdir()) if p.is_file()}

    repair_stale_tails.run(apply=True, rows=rows)

    after = {p.name: p.read_bytes() for p in list(user.iterdir()) + list((user / ".versions").iterdir()) if p.is_file()}
    assert after == before

"""Regression tests for the empty-upload-over-non-empty-metadata guard in
`finalize_upload` (app/routers/files.py).

Background: the production server was found holding zero-byte saves with no
earlier versions (the files had been emptied on devices, e.g. a stray copy of
a save tree being synced instead of the real one). This is the last line of
defense: even if a buggy or malicious
client sends a finalize claiming size 0 for a path that already has real,
non-empty content, the server must refuse rather than silently erase it.

New files with no prior metadata remain allowed, since legitimately-empty
saves exist and must be accepted the first time.

This also covers the "did upload_fragment already clobber the blob"
question: upload_fragment() opens the on-disk blob "r+b" (no truncate on
open), but the client always encrypts at least one block even for a 0-byte
plaintext, so an empty upload still writes ciphertext at offset 0 before
finalize ever runs. The guard must restore the pre-upload snapshot that
version_manager.begin_upload() took, not just refuse to update metadata.

Mirrors the TestClient + dependency-override + monkeypatch pattern used in
tests/test_delete_versioning.py: no real DB or filesystem outside tmp_path
is touched.
"""
import os
from contextlib import contextmanager
from unittest.mock import MagicMock

# Neuter DB init before importing the app so TestClient's startup doesn't
# spin trying to reach a real Postgres pool.
from app import database as _db  # noqa: E402
_db.get_pool = lambda: MagicMock()
_db.init_db = lambda: None

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app
from app.dependencies import get_current_user
from app.routers import files as files_router
from app import utils as utils_module
from app.services.version_manager import version_manager


def _fake_user():
    return {"id": 1, "email": "t@x"}


@contextmanager
def _fake_get_db():
    yield MagicMock()


app.dependency_overrides[get_current_user] = _fake_user
client = TestClient(app)

# The app's double-SHA-256 of empty input, per production evidence. Its exact
# value doesn't matter to these tests — it's just a stand-in hash string.
EMPTY_HASH = "5df6e0e2761359d30a8275058e299fcc0381534545f55cf43e41983f5d4c9456"


def _patch_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(files_router, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(utils_module, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(version_manager, "storage_root", str(tmp_path))


def _record_upserts(monkeypatch):
    """Monkeypatches crud.upsert_file_metadata to record (size,) tuples instead
    of touching a real DB, and returns the list of recorded sizes."""
    calls = []
    monkeypatch.setattr(
        files_router.crud, "upsert_file_metadata",
        lambda conn, uid, path, hash, size, updated_at, device_name, blocks: calls.append(size),
    )
    return calls


def test_empty_upload_over_nonempty_metadata_is_rejected_and_blob_restored(tmp_path, monkeypatch):
    user_id = 1
    rel_path = "switch/0100000000000000/rep_gamedata1.dat"
    good_content = b"G" * 4012

    _patch_storage(tmp_path, monkeypatch)

    abs_path = os.path.join(str(tmp_path), str(user_id), rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(good_content)

    # Simulate what upload_fragment's begin_upload() already did before this
    # finalize call: snapshot the good pre-upload content.
    version_manager.create_version(user_id, rel_path, "OldDevice")
    assert len(version_manager.list_versions(user_id, rel_path)) == 1

    # Simulate upload_fragment's partial clobber: even a logically-empty
    # upload still writes one encrypted block's worth of ciphertext at
    # offset 0 (the client always encodes at least one block). This leaves
    # the blob already damaged by the time finalize runs.
    with open(abs_path, "r+b") as f:
        f.write(b"\x00" * 64)
    assert open(abs_path, "rb").read() != good_content, "test setup: blob should be clobbered before finalize"

    existing_meta = {"hash": "oldhash", "size": len(good_content), "updated_at": 1000, "device_name": "OldDevice"}
    monkeypatch.setattr(files_router, "get_db", _fake_get_db)
    monkeypatch.setattr(files_router.crud, "get_file_metadata", lambda conn, uid, path: existing_meta)
    upsert_calls = _record_upserts(monkeypatch)

    resp = client.post("/api/v1/upload/finalize", json={
        "path": rel_path, "hash": EMPTY_HASH, "size": 0, "updated_at": 2000, "device_name": "NewDevice",
    })

    assert resp.status_code == 409, resp.text
    assert upsert_calls == [], "metadata must not be modified when the empty upload is refused"

    with open(abs_path, "rb") as f:
        restored_content = f.read()
    assert restored_content == good_content, "blob must be restored to its pre-clobber state, not left damaged"


def test_empty_upload_over_nonempty_metadata_rejected_even_without_prior_snapshot(tmp_path, monkeypatch):
    """If no snapshot exists yet (e.g. finalize called with no preceding
    upload_fragment), the guard must still reject and must not crash trying
    to restore from a version that doesn't exist."""
    user_id = 1
    rel_path = "switch/0100000000000000/other_save.dat"
    good_content = b"H" * 2048

    _patch_storage(tmp_path, monkeypatch)

    abs_path = os.path.join(str(tmp_path), str(user_id), rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(good_content)

    existing_meta = {"hash": "oldhash", "size": len(good_content), "updated_at": 1000, "device_name": "OldDevice"}
    monkeypatch.setattr(files_router, "get_db", _fake_get_db)
    monkeypatch.setattr(files_router.crud, "get_file_metadata", lambda conn, uid, path: existing_meta)
    upsert_calls = _record_upserts(monkeypatch)

    resp = client.post("/api/v1/upload/finalize", json={
        "path": rel_path, "hash": EMPTY_HASH, "size": 0, "updated_at": 2000, "device_name": "NewDevice",
    })

    assert resp.status_code == 409, resp.text
    assert upsert_calls == []
    with open(abs_path, "rb") as f:
        assert f.read() == good_content, "blob was never clobbered in this scenario, so it must remain as-is"


def test_empty_new_file_with_no_existing_metadata_is_accepted(tmp_path, monkeypatch):
    """A legitimately empty save, uploaded for the first time (no existing
    metadata row), must still be accepted."""
    user_id = 1
    rel_path = "ps2/newsave/empty.bin"

    _patch_storage(tmp_path, monkeypatch)

    abs_path = os.path.join(str(tmp_path), str(user_id), rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    open(abs_path, "wb").close()  # genuinely 0 bytes

    monkeypatch.setattr(files_router, "get_db", _fake_get_db)
    monkeypatch.setattr(files_router.crud, "get_file_metadata", lambda conn, uid, path: None)
    upsert_calls = _record_upserts(monkeypatch)

    resp = client.post("/api/v1/upload/finalize", json={
        "path": rel_path, "hash": EMPTY_HASH, "size": 0, "updated_at": 1000, "device_name": "NewDevice",
    })

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"message": "Finalized"}
    assert upsert_calls == [0], "a legitimately-new empty file must still be recorded"


def test_nonempty_upload_over_empty_metadata_is_accepted(tmp_path, monkeypatch):
    """A real, non-empty upload replacing a previously-empty server copy is
    exactly the case the client-side fix restores — must not be blocked."""
    user_id = 1
    rel_path = "ps2/save/growing.bin"
    content = b"real content now"

    _patch_storage(tmp_path, monkeypatch)

    abs_path = os.path.join(str(tmp_path), str(user_id), rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    # Simulate upload_fragment having already written the real (non-empty) bytes.
    with open(abs_path, "wb") as f:
        f.write(content)

    existing_meta = {"hash": EMPTY_HASH, "size": 0, "updated_at": 500, "device_name": "OldDevice"}
    monkeypatch.setattr(files_router, "get_db", _fake_get_db)
    monkeypatch.setattr(files_router.crud, "get_file_metadata", lambda conn, uid, path: existing_meta)
    upsert_calls = _record_upserts(monkeypatch)

    resp = client.post("/api/v1/upload/finalize", json={
        "path": rel_path, "hash": "newhash", "size": len(content), "updated_at": 1500, "device_name": "NewDevice",
    })

    assert resp.status_code == 200, resp.text
    assert upsert_calls == [len(content)]

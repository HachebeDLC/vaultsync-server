"""Regression test for the delete-then-restore bug.

`delete_file` used to enqueue `version_manager.create_version` as a
BackgroundTask, which FastAPI only runs *after* the response is sent — by
then `os.remove(safe_path)` had already deleted the source file, so
`create_version` (which bails out with `if not os.path.exists(source_path):
return`) silently produced no version. This proves a DELETE call now leaves
a restorable version before the file disappears from disk.

Mirrors the TestClient + dependency-override + monkeypatch pattern used in
tests/test_romm_pull_endpoint.py: no real DB or filesystem outside tmp_path
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


def test_delete_leaves_a_restorable_version(tmp_path, monkeypatch):
    user_id = 1
    rel_path = "switch/0100000000000000/save.bin"
    content = b"vault-file-contents-before-delete"

    # Point storage (both the router's and is_safe_path's copy of it) and the
    # version manager's root at a throwaway tmp_path for this test.
    monkeypatch.setattr(files_router, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(utils_module, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(version_manager, "storage_root", str(tmp_path))

    abs_path = os.path.join(str(tmp_path), str(user_id), rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(content)

    fake_metadata = {"device_name": "TestDevice"}
    monkeypatch.setattr(files_router, "get_db", _fake_get_db)
    monkeypatch.setattr(
        files_router.crud, "get_file_metadata",
        lambda conn, uid, path: fake_metadata,
    )
    monkeypatch.setattr(
        files_router.crud, "delete_file_metadata",
        lambda conn, uid, path: None,
    )

    resp = client.request("DELETE", "/api/v1/files", json={"filename": rel_path})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"message": "Deleted"}
    assert not os.path.exists(abs_path), "file should be removed from disk after delete"

    versions = version_manager.list_versions(user_id, rel_path)
    assert len(versions) == 1, f"expected exactly one restorable version, got {versions}"
    assert versions[0]["device_name"] == "TestDevice"
    assert versions[0]["size"] == len(content)


def test_delete_without_prior_metadata_creates_no_version(tmp_path, monkeypatch):
    """If there was no metadata row (e.g. already deleted), no version is created,
    and the delete still succeeds without raising."""
    user_id = 1
    rel_path = "switch/0100000000000001/orphan.bin"

    monkeypatch.setattr(files_router, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(utils_module, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(version_manager, "storage_root", str(tmp_path))

    monkeypatch.setattr(files_router, "get_db", _fake_get_db)
    monkeypatch.setattr(files_router.crud, "get_file_metadata", lambda conn, uid, path: None)
    monkeypatch.setattr(files_router.crud, "delete_file_metadata", lambda conn, uid, path: None)

    resp = client.request("DELETE", "/api/v1/files", json={"filename": rel_path})

    assert resp.status_code == 200, resp.text
    assert version_manager.list_versions(user_id, rel_path) == []

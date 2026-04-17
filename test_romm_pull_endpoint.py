"""Endpoint-level coverage for `/api/v1/romm/pull`.

Uses FastAPI's `TestClient` with dependency overrides so no real DB or RomM
instance is needed. `RomMClient.pull_save_from_romm` is monkey-patched to avoid
the httpx layer — unit-level httpx coverage lives in `test_romm_pull.py`.
"""
import os
import hashlib
import tempfile
import urllib.parse
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

# Neuter DB init before importing the app so TestClient's startup doesn't
# spin 60s trying to reach the real Postgres pool.
from app import database as _db  # noqa: E402
_db.get_pool = lambda: MagicMock()
_db.init_db = lambda: None

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app
from app.dependencies import get_current_user
from app.services.romm_client import (
    RommNotFound,
    RommUpstreamError,
    RommUnavailable,
)
from app.services import romm_client as romm_client_module
from app.routers import files as files_router


def _fake_user():
    # Bypass JWT auth. `romm_url` / `romm_api_key` unset → endpoint uses the
    # global `romm_client` (which we patch below).
    return {"id": 1, "email": "t@x", "romm_url": None, "romm_api_key": None}


@contextmanager
def _fake_get_db():
    yield MagicMock()


# Patch `get_db` in the router module so no real DB pool is required.
files_router.get_db = _fake_get_db

app.dependency_overrides[get_current_user] = _fake_user
client = TestClient(app)


def _temp_with(bytes_: bytes) -> str:
    fd, path = tempfile.mkstemp(prefix="romm_pull_test_")
    with os.fdopen(fd, "wb") as f:
        f.write(bytes_)
    return path


def test_happy_path_streams_bytes_and_headers():
    print("→ endpoint happy path")
    payload = b"SAVE_PLAINTEXT_BYTES" * 128
    tmp = _temp_with(payload)
    meta = {
        "save_id": 42, "romm_id": 100, "file_name": "My Save (USA).sav",
        "updated_at": "2026-04-17T00:00:00Z", "emulator": "snes9x",
        "size": len(payload),
    }

    with patch.object(
        romm_client_module.romm_client,
        "pull_save_from_romm",
        new=AsyncMock(return_value=(tmp, meta)),
    ):
        r = client.post("/api/v1/romm/pull", json={"rom_id": 100})

    assert r.status_code == 200, r.text
    assert r.content == payload
    assert r.headers["x-romm-save-id"] == "42"
    assert r.headers["x-romm-rom-id"] == "100"
    # file_name must be URL-encoded (spaces/parens/etc in RomM names)
    assert r.headers["x-romm-file-name"] == urllib.parse.quote("My Save (USA).sav")
    assert r.headers["x-romm-size"] == str(len(payload))
    assert r.headers["x-romm-updated-at"] == "2026-04-17T00:00:00Z"
    assert r.headers["x-romm-emulator"] == "snes9x"
    assert r.headers["x-romm-sha256"] == hashlib.sha256(payload).hexdigest()
    # Temp file should have been removed by the stream generator's `finally`.
    assert not os.path.exists(tmp), "temp file should be deleted after streaming"
    print("  ok")


def test_optional_headers_omitted_when_meta_missing():
    print("→ optional headers omitted when emulator/updated_at missing")
    payload = b"short"
    tmp = _temp_with(payload)
    meta = {
        "save_id": 5, "romm_id": 50, "file_name": "x.sav",
        "updated_at": None, "emulator": None, "size": len(payload),
    }
    with patch.object(
        romm_client_module.romm_client,
        "pull_save_from_romm",
        new=AsyncMock(return_value=(tmp, meta)),
    ):
        r = client.post("/api/v1/romm/pull", json={"rom_id": 50})
    assert r.status_code == 200
    assert "x-romm-updated-at" not in {k.lower() for k in r.headers.keys()}
    assert "x-romm-emulator" not in {k.lower() for k in r.headers.keys()}
    assert not os.path.exists(tmp)
    print("  ok")


def test_not_found_maps_to_404():
    print("→ RommNotFound → HTTP 404")
    with patch.object(
        romm_client_module.romm_client,
        "pull_save_from_romm",
        new=AsyncMock(side_effect=RommNotFound("no save")),
    ):
        r = client.post("/api/v1/romm/pull", json={"rom_id": 1})
    assert r.status_code == 404, r.text
    print("  ok")


def test_upstream_error_maps_to_502():
    print("→ RommUpstreamError → HTTP 502")
    with patch.object(
        romm_client_module.romm_client,
        "pull_save_from_romm",
        new=AsyncMock(side_effect=RommUpstreamError("bad gateway")),
    ):
        r = client.post("/api/v1/romm/pull", json={"rom_id": 1})
    assert r.status_code == 502, r.text
    print("  ok")


def test_unavailable_maps_to_503():
    print("→ RommUnavailable → HTTP 503")
    with patch.object(
        romm_client_module.romm_client,
        "pull_save_from_romm",
        new=AsyncMock(side_effect=RommUnavailable("timeout")),
    ):
        r = client.post("/api/v1/romm/pull", json={"rom_id": 1})
    assert r.status_code == 503, r.text
    print("  ok")


def test_auth_required_when_override_removed():
    print("→ without override, endpoint rejects anonymous callers")
    app.dependency_overrides.pop(get_current_user, None)
    try:
        r = client.post("/api/v1/romm/pull", json={"rom_id": 1})
        assert r.status_code in (401, 403), f"expected auth rejection, got {r.status_code}"
    finally:
        app.dependency_overrides[get_current_user] = _fake_user
    print("  ok")


def main():
    test_happy_path_streams_bytes_and_headers()
    test_optional_headers_omitted_when_meta_missing()
    test_not_found_maps_to_404()
    test_upstream_error_maps_to_502()
    test_unavailable_maps_to_503()
    test_auth_required_when_override_removed()
    print("\nAll /api/v1/romm/pull endpoint tests passed.")


if __name__ == "__main__":
    main()

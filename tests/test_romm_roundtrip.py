"""End-to-end integration test for `/api/v1/romm/pull`.

Wires a mock RomM FastAPI app to `RomMClient`'s httpx calls via `httpx.ASGITransport`.
No monkey-patching of `pull_save_from_romm` — the full code path from vaultsync's
endpoint → `RomMClient` → (real httpx stack) → mock RomM is exercised.

Byte-parity is asserted by seeding the mock RomM with a known blob and comparing
the response body + sha256 header to the source.
"""
import hashlib
import os
import urllib.parse
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from fastapi import FastAPI, Query, Response
import httpx

from app import database as _db  # noqa: E402
_db.get_pool = lambda: MagicMock()
_db.init_db = lambda: None

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app
from app.dependencies import get_current_user
from app.services import romm_client as romm_client_module
from app.routers import files as files_router


# ---------------------------------------------------------------------------
# Mock RomM — speaks just enough of the RomM API to satisfy
# `ensure_device_registered` + `pull_save_from_romm`.
# ---------------------------------------------------------------------------
SAVE_BYTES = b"ROUND_TRIP_PAYLOAD__" + os.urandom(4096)
SAVE_META = {
    "id": 777,
    "file_name": "Kirby & The Amazing Mirror (USA).sav",
    "updated_at": "2026-04-17T10:00:00Z",
    "emulator": "mgba",
}
KNOWN_ROM_ID = 12345

mock_romm = FastAPI()


@mock_romm.get("/api/heartbeat")
async def _heartbeat():
    return {"SYSTEM": {"VERSION": "4.8.1"}}


@mock_romm.post("/api/devices")
async def _register_device():
    return {"device_id": "mock-device-id"}


@mock_romm.get("/api/saves")
async def _list_saves(rom_id: int = Query(...), device_id: str | None = None):
    if rom_id == KNOWN_ROM_ID:
        return [SAVE_META]
    if rom_id == 404_404:
        # Simulate RomM 404 on the list endpoint
        return Response(status_code=404)
    if rom_id == 500_500:
        return Response(status_code=502)
    return []


@mock_romm.get("/api/saves/{save_id}/content/{file_name}")
async def _content(save_id: int, file_name: str, device_id: str | None = None):
    if save_id == SAVE_META["id"]:
        return Response(content=SAVE_BYTES, media_type="application/octet-stream")
    return Response(status_code=404)


# ---------------------------------------------------------------------------
# Wire RomMClient's httpx.AsyncClient to the mock RomM via ASGITransport.
# ---------------------------------------------------------------------------
_RealAsyncClient = httpx.AsyncClient


class _ASGIRoutedAsyncClient(_RealAsyncClient):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("transport", httpx.ASGITransport(app=mock_romm))
        super().__init__(*args, **kwargs)


# ---------------------------------------------------------------------------
# Neuter DB/CRUD so the endpoint has everything it needs without a real DB.
# ---------------------------------------------------------------------------
@contextmanager
def _fake_get_db():
    yield MagicMock()


files_router.get_db = _fake_get_db


def _fake_user():
    # Force the endpoint to use the module-level `romm_client` (which we
    # reconfigure below) by leaving user-scoped creds unset.
    return {"id": 1, "email": "t@x", "romm_url": None, "romm_api_key": None}


app.dependency_overrides[get_current_user] = _fake_user
client = TestClient(app)


def _install_fake_crud():
    import app.crud as crud_mod
    crud_mod.get_user_romm_device = lambda conn, uid: (None, None)
    crud_mod.set_user_romm_device = lambda conn, uid, did, ver: None


def _reset_module_romm_client():
    """Point the module-level `romm_client` at our mock-backed config."""
    rc = romm_client_module.RomMClient("http://romm.mock", "mock-key")
    romm_client_module.romm_client = rc
    # The router imported `romm_client` by name into its namespace; refresh it.
    files_router.romm_client = rc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_pull_roundtrip_byte_parity():
    print("→ E2E pull: seeded RomM bytes come back unchanged via vaultsync")
    _install_fake_crud()
    _reset_module_romm_client()

    with patch.object(romm_client_module.httpx, "AsyncClient", _ASGIRoutedAsyncClient):
        r = client.post("/api/v1/romm/pull", json={"rom_id": KNOWN_ROM_ID})

    assert r.status_code == 200, r.text
    assert r.content == SAVE_BYTES, "byte-parity failed end-to-end"

    expected_sha = hashlib.sha256(SAVE_BYTES).hexdigest()
    assert r.headers["x-romm-sha256"] == expected_sha
    assert r.headers["x-romm-save-id"] == str(SAVE_META["id"])
    assert r.headers["x-romm-rom-id"] == str(KNOWN_ROM_ID)
    assert r.headers["x-romm-size"] == str(len(SAVE_BYTES))
    assert r.headers["x-romm-emulator"] == "mgba"
    assert r.headers["x-romm-updated-at"] == "2026-04-17T10:00:00Z"

    expected_name = urllib.parse.quote(SAVE_META["file_name"])
    assert r.headers["x-romm-file-name"] == expected_name
    assert urllib.parse.unquote(r.headers["x-romm-file-name"]) == SAVE_META["file_name"]
    print("  ok — bytes + headers match source")


def test_pull_roundtrip_empty_saves_list_maps_to_404():
    print("→ E2E pull: RomM returns empty list → 404")
    _install_fake_crud()
    _reset_module_romm_client()

    with patch.object(romm_client_module.httpx, "AsyncClient", _ASGIRoutedAsyncClient):
        r = client.post("/api/v1/romm/pull", json={"rom_id": 99999})

    assert r.status_code == 404, r.text
    print("  ok")


def test_pull_roundtrip_romm_404_maps_to_404():
    print("→ E2E pull: RomM responds 404 on list → 404")
    _install_fake_crud()
    _reset_module_romm_client()

    with patch.object(romm_client_module.httpx, "AsyncClient", _ASGIRoutedAsyncClient):
        r = client.post("/api/v1/romm/pull", json={"rom_id": 404_404})

    assert r.status_code == 404, r.text
    print("  ok")


def test_pull_roundtrip_romm_5xx_maps_to_502():
    print("→ E2E pull: RomM upstream 5xx → 502")
    _install_fake_crud()
    _reset_module_romm_client()

    with patch.object(romm_client_module.httpx, "AsyncClient", _ASGIRoutedAsyncClient):
        r = client.post("/api/v1/romm/pull", json={"rom_id": 500_500})

    assert r.status_code == 502, r.text
    print("  ok")


def main():
    test_pull_roundtrip_byte_parity()
    test_pull_roundtrip_empty_saves_list_maps_to_404()
    test_pull_roundtrip_romm_404_maps_to_404()
    test_pull_roundtrip_romm_5xx_maps_to_502()
    print("\nAll /api/v1/romm/pull round-trip tests passed.")


if __name__ == "__main__":
    main()

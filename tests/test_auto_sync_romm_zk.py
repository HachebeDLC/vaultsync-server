"""Zero-knowledge regression test for the RomM auto-sync background task.

`_process_user_romm_sync` used to download the plaintext save from RomM and
`shutil.move` it straight into the user's encrypted vault, then upsert file
metadata — writing plaintext into the ZK vault and bypassing the client's
local encryption entirely. It must instead just notify the user's connected
devices (`event_notifier.broadcast_to_user`, event `romm_save_newer`) and
leave storage/DB untouched; the client is responsible for pulling via
`/api/v1/romm/pull` and re-uploading through the normal encrypted flow.

Mirrors the monkeypatching style of tests/test_romm_pull_endpoint.py and
tests/test_delete_versioning.py: no real DB, RomM instance, or filesystem
writes outside tmp_path.
"""
import os
from contextlib import contextmanager
from unittest.mock import MagicMock

import httpx
import pytest

from app.services import auto_sync_romm


class _FakeResp:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


def _make_fake_async_client(saves):
    class _FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None, timeout=None):
            return _FakeResp(200, saves)

    return _FakeAsyncClient


class _FakeRommClient:
    def __init__(self, base_url="http://romm.local", api_key="key"):
        self.base_url = base_url
        self.headers = {"Authorization": f"Bearer {api_key}"}


@contextmanager
def _fake_get_db():
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = ("http://romm.local", "apikey123")
    conn.cursor.return_value = cursor
    yield conn


def _install_common_patches(monkeypatch, mapped_files, saves, storage_dir):
    monkeypatch.setattr(auto_sync_romm, "get_db", _fake_get_db)
    monkeypatch.setattr(auto_sync_romm, "get_files_with_romm_id", lambda conn, user_id: mapped_files)
    monkeypatch.setattr(auto_sync_romm, "get_romm_client", lambda url, key: _FakeRommClient(url, key))
    monkeypatch.setattr(httpx, "AsyncClient", _make_fake_async_client(saves))

    broadcasts = []

    async def _fake_broadcast(user_id, payload, event="file_available", target_device=None):
        broadcasts.append({"user_id": user_id, "payload": payload, "event": event})

    monkeypatch.setattr(auto_sync_romm.event_notifier, "broadcast_to_user", _fake_broadcast)

    # Guard against any accidental server-side write to the vault or DB.
    from app import crud as crud_module
    upsert_calls = []
    get_meta_calls = []
    monkeypatch.setattr(crud_module, "upsert_file_metadata", lambda *a, **kw: upsert_calls.append((a, kw)))
    monkeypatch.setattr(crud_module, "get_file_metadata", lambda *a, **kw: get_meta_calls.append((a, kw)))

    return broadcasts, upsert_calls, get_meta_calls


@pytest.mark.asyncio
async def test_newer_romm_save_broadcasts_and_writes_nothing(monkeypatch, tmp_path):
    print("-> newer RomM save triggers exactly one romm_save_newer broadcast, no writes")
    user_id = 7
    path = "snes/Super Game (USA)/save.srm"
    local_updated_at = 1_000_000  # epoch ms
    mapped_files = [{"path": path, "romm_id": 555, "updated_at": local_updated_at}]

    # RomM reports a save updated well past the 5s tolerance.
    romm_updated_iso = "2026-04-17T12:00:10Z"
    saves = [{"updated_at": romm_updated_iso}]

    broadcasts, upsert_calls, get_meta_calls = _install_common_patches(
        monkeypatch, mapped_files, saves, str(tmp_path)
    )

    fake_storage = tmp_path / "storage"
    fake_storage.mkdir()

    await auto_sync_romm._process_user_romm_sync(user_id)

    assert len(broadcasts) == 1, f"expected exactly one broadcast, got {broadcasts}"
    b = broadcasts[0]
    assert b["event"] == "romm_save_newer"
    assert b["user_id"] == user_id

    from datetime import datetime
    expected_romm_updated_at = int(datetime.fromisoformat(romm_updated_iso.replace("Z", "+00:00")).timestamp() * 1000)

    assert b["payload"] == {
        "type": "romm_save_newer",
        "path": path,
        "system_id": "snes",
        "romm_id": 555,
        "romm_updated_at": expected_romm_updated_at,
    }

    # Nothing written: fake storage dir stays empty, no DB upsert/get.
    assert list(fake_storage.iterdir()) == [], "server must not write to the vault on a ZK pull notification"
    assert upsert_calls == [], "server must not upsert file metadata during a ZK pull notification"
    assert get_meta_calls == []
    print("  ok")


@pytest.mark.asyncio
async def test_older_or_equal_romm_save_does_not_broadcast(monkeypatch, tmp_path):
    print("-> older/equal RomM save produces no broadcast")
    user_id = 8
    path = "gba/Some Game (USA)/save.sav"

    from datetime import datetime
    romm_updated_iso = "2026-04-17T12:00:00Z"
    romm_updated_at = int(datetime.fromisoformat(romm_updated_iso.replace("Z", "+00:00")).timestamp() * 1000)
    # Local is already newer than (well outside the 5s tolerance of) RomM.
    local_updated_at = romm_updated_at + 60_000
    mapped_files = [{"path": path, "romm_id": 321, "updated_at": local_updated_at}]

    saves = [{"updated_at": romm_updated_iso}]

    broadcasts, upsert_calls, get_meta_calls = _install_common_patches(
        monkeypatch, mapped_files, saves, str(tmp_path)
    )

    fake_storage = tmp_path / "storage"
    fake_storage.mkdir()

    await auto_sync_romm._process_user_romm_sync(user_id)

    assert broadcasts == [], f"expected no broadcast for an older/equal RomM save, got {broadcasts}"
    assert list(fake_storage.iterdir()) == []
    assert upsert_calls == []
    assert get_meta_calls == []
    print("  ok")

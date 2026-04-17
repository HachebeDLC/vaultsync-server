"""Unit coverage for `RomMClient.pull_save_from_romm` and the error surface it
feeds to `/api/v1/romm/pull`.

Matches the in-repo convention: runnable `async def` script driven by
`unittest.mock`, not pytest/respx. Run via `python test_romm_pull.py`.
"""
import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.romm_client import (
    RomMClient,
    RommError,
    RommNotFound,
    RommUpstreamError,
    RommUnavailable,
)
import httpx


def _mock_conn():
    """A MagicMock conn; `ensure_device_registered` is stubbed so DB isn't touched."""
    return MagicMock()


def _install_httpx_get(mock_client_class, route_handler):
    """Wires `httpx.AsyncClient().__aenter__().get()` to `route_handler(url, **kw)`."""
    instance = mock_client_class.return_value.__aenter__.return_value
    instance.get.side_effect = route_handler
    return instance


async def test_happy_path():
    print("→ happy path: latest save picked, temp file written, metadata returned")
    client = RomMClient("http://romm.test", "key")
    # Short-circuit device registration — doesn't touch DB
    client.ensure_device_registered = AsyncMock(return_value=None)

    list_resp = AsyncMock()
    list_resp.status_code = 200
    list_resp.json = lambda: [
        {"id": 11, "file_name": "old.sav", "updated_at": "2026-04-10T00:00:00Z"},
        {"id": 22, "file_name": "new.sav", "updated_at": "2026-04-17T00:00:00Z",
         "emulator": "snes9x"},
    ]
    dl_resp = AsyncMock()
    dl_resp.status_code = 200
    dl_resp.content = b"SAVE_BYTES_PLAINTEXT" * 64

    async def route(url, **_):
        if "/content" in url:
            return dl_resp
        return list_resp

    with patch("httpx.AsyncClient") as mock_client:
        _install_httpx_get(mock_client, route)
        tmp_path, meta = await client.pull_save_from_romm(_mock_conn(), 555, user_id=1)

    try:
        assert os.path.exists(tmp_path), "temp file should exist on success"
        with open(tmp_path, "rb") as f:
            assert f.read() == b"SAVE_BYTES_PLAINTEXT" * 64
        assert meta["save_id"] == 22, f"latest updated_at should win, got {meta['save_id']}"
        assert meta["file_name"] == "new.sav"
        assert meta["emulator"] == "snes9x"
        assert meta["romm_id"] == 555
        assert meta["size"] == len(b"SAVE_BYTES_PLAINTEXT" * 64)
        print("  ok")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


async def test_404_on_list():
    print("→ RomM returns 404 for the rom_id → RommNotFound")
    client = RomMClient("http://romm.test", "key")
    client.ensure_device_registered = AsyncMock(return_value=None)

    resp = AsyncMock(); resp.status_code = 404; resp.json = lambda: {}

    async def route(url, **_):
        return resp

    with patch("httpx.AsyncClient") as mock_client:
        _install_httpx_get(mock_client, route)
        try:
            await client.pull_save_from_romm(_mock_conn(), 999, user_id=1)
            raise AssertionError("expected RommNotFound")
        except RommNotFound:
            print("  ok")


async def test_empty_saves_list():
    print("→ RomM returns 200 but no saves → RommNotFound")
    client = RomMClient("http://romm.test", "key")
    client.ensure_device_registered = AsyncMock(return_value=None)

    resp = AsyncMock(); resp.status_code = 200; resp.json = lambda: []

    async def route(url, **_):
        return resp

    with patch("httpx.AsyncClient") as mock_client:
        _install_httpx_get(mock_client, route)
        try:
            await client.pull_save_from_romm(_mock_conn(), 1, user_id=1)
            raise AssertionError("expected RommNotFound")
        except RommNotFound:
            print("  ok")


async def test_5xx_on_list():
    print("→ RomM returns 500 listing saves → RommUpstreamError")
    client = RomMClient("http://romm.test", "key")
    client.ensure_device_registered = AsyncMock(return_value=None)

    resp = AsyncMock(); resp.status_code = 502; resp.json = lambda: {}

    async def route(url, **_):
        return resp

    with patch("httpx.AsyncClient") as mock_client:
        _install_httpx_get(mock_client, route)
        try:
            await client.pull_save_from_romm(_mock_conn(), 1, user_id=1)
            raise AssertionError("expected RommUpstreamError")
        except RommUpstreamError:
            print("  ok")


async def test_5xx_on_download():
    print("→ listing OK but download returns 500 → RommUpstreamError")
    client = RomMClient("http://romm.test", "key")
    client.ensure_device_registered = AsyncMock(return_value=None)

    list_resp = AsyncMock()
    list_resp.status_code = 200
    list_resp.json = lambda: [{"id": 7, "file_name": "s.sav", "updated_at": "2026-04-17"}]

    dl_resp = AsyncMock(); dl_resp.status_code = 503; dl_resp.content = b""

    async def route(url, **_):
        return dl_resp if "/content" in url else list_resp

    with patch("httpx.AsyncClient") as mock_client:
        _install_httpx_get(mock_client, route)
        try:
            await client.pull_save_from_romm(_mock_conn(), 1, user_id=1)
            raise AssertionError("expected RommUpstreamError")
        except RommUpstreamError:
            print("  ok")


async def test_download_404_fallback():
    print("→ /content/<name> 404 → client retries bare /content URL")
    client = RomMClient("http://romm.test", "key")
    client.ensure_device_registered = AsyncMock(return_value=None)

    list_resp = AsyncMock()
    list_resp.status_code = 200
    list_resp.json = lambda: [{"id": 7, "file_name": "s.sav", "updated_at": "2026-04-17"}]

    dl_404 = AsyncMock(); dl_404.status_code = 404; dl_404.content = b""
    dl_ok = AsyncMock(); dl_ok.status_code = 200; dl_ok.content = b"FALLBACK_OK"

    calls = []

    async def route(url, **_):
        calls.append(url)
        if "/content/" in url:  # named path -> 404
            return dl_404
        if url.endswith("/content"):  # fallback bare path -> 200
            return dl_ok
        return list_resp

    with patch("httpx.AsyncClient") as mock_client:
        _install_httpx_get(mock_client, route)
        tmp_path, meta = await client.pull_save_from_romm(_mock_conn(), 1, user_id=1)

    try:
        with open(tmp_path, "rb") as f:
            assert f.read() == b"FALLBACK_OK"
        assert any(u.endswith("/content/s.sav") for u in calls), "should try named path first"
        assert any(u.endswith("/content") for u in calls), "should fall back to bare /content"
        print("  ok")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


async def test_connect_error_maps_to_unavailable():
    print("→ httpx.ConnectError → RommUnavailable")
    client = RomMClient("http://romm.test", "key")
    client.ensure_device_registered = AsyncMock(return_value=None)

    async def route(url, **_):
        raise httpx.ConnectError("dns")

    with patch("httpx.AsyncClient") as mock_client:
        _install_httpx_get(mock_client, route)
        try:
            await client.pull_save_from_romm(_mock_conn(), 1, user_id=1)
            raise AssertionError("expected RommUnavailable")
        except RommUnavailable:
            print("  ok")


async def test_timeout_maps_to_unavailable():
    print("→ httpx.TimeoutException → RommUnavailable")
    client = RomMClient("http://romm.test", "key")
    client.ensure_device_registered = AsyncMock(return_value=None)

    async def route(url, **_):
        raise httpx.TimeoutException("slow")

    with patch("httpx.AsyncClient") as mock_client:
        _install_httpx_get(mock_client, route)
        try:
            await client.pull_save_from_romm(_mock_conn(), 1, user_id=1)
            raise AssertionError("expected RommUnavailable")
        except RommUnavailable:
            print("  ok")


async def test_unconfigured_client():
    print("→ client with no api_key → RommUnavailable (no network attempted)")
    client = RomMClient("http://romm.test", "")
    try:
        await client.pull_save_from_romm(_mock_conn(), 1, user_id=1)
        raise AssertionError("expected RommUnavailable")
    except RommUnavailable:
        print("  ok")


async def test_exception_hierarchy():
    print("→ RommNotFound / UpstreamError / Unavailable all subclass RommError")
    assert issubclass(RommNotFound, RommError)
    assert issubclass(RommUpstreamError, RommError)
    assert issubclass(RommUnavailable, RommError)
    print("  ok")


async def main():
    await test_happy_path()
    await test_404_on_list()
    await test_empty_saves_list()
    await test_5xx_on_list()
    await test_5xx_on_download()
    await test_download_404_fallback()
    await test_connect_error_maps_to_unavailable()
    await test_timeout_maps_to_unavailable()
    await test_unconfigured_client()
    await test_exception_hierarchy()
    print("\nAll pull_save_from_romm tests passed.")


if __name__ == "__main__":
    asyncio.run(main())

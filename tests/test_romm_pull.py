"""Unit coverage for `RomMClient.pull_save_from_romm` and the error surface it
feeds to `/api/v1/romm/pull`.

Matches the in-repo convention: runnable `async def` script driven by
`unittest.mock`, not pytest/respx. Run via `python test_romm_pull.py`.
"""
import asyncio
import os
from contextlib import asynccontextmanager
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


def _setup_mock_client(mock_client_class, get_handler=None, stream_routes=None):
    """
    Configures the httpx.AsyncClient mock for the persistent-client pattern.

    get_handler:   sync or async callable(url, **kw) -> response mock (for GET requests)
    stream_routes: sync callable(url) -> (status_code, bytes)            (for stream())

    Returns the mock instance.
    """
    instance = mock_client_class.return_value
    instance.is_closed = False

    if get_handler:
        # Must be AsyncMock so that `await client.get(...)` resolves correctly.
        instance.get = AsyncMock(side_effect=get_handler)

    if stream_routes:
        @asynccontextmanager
        async def _stream_cm(method, url, **_kw):
            status, content = stream_routes(url)
            resp = MagicMock()
            resp.status_code = status

            async def aiter_bytes(chunk_size=65536):
                if content:
                    yield content

            resp.aiter_bytes = aiter_bytes
            yield resp

        instance.stream.side_effect = lambda *a, **kw: _stream_cm(*a, **kw)

    return instance


async def test_happy_path():
    print("→ happy path: latest save picked, temp file written, metadata returned")
    client = RomMClient("http://romm.test", "key")
    client.ensure_device_registered = AsyncMock(return_value=None)

    list_resp = AsyncMock()
    list_resp.status_code = 200
    list_resp.json = lambda: [
        {"id": 11, "file_name": "old.sav", "updated_at": "2026-04-10T00:00:00Z"},
        {"id": 22, "file_name": "new.sav", "updated_at": "2026-04-17T00:00:00Z",
         "emulator": "snes9x"},
    ]

    expected_bytes = b"SAVE_BYTES_PLAINTEXT" * 64

    def stream_routes(url):
        if "/content" in url:
            return (200, expected_bytes)
        return (404, b"")

    with patch("app.services.romm_client.httpx.AsyncClient") as mock_client_class:
        _setup_mock_client(
            mock_client_class,
            get_handler=lambda url, **_: list_resp,
            stream_routes=stream_routes,
        )
        tmp_path, meta = await client.pull_save_from_romm(555)

    try:
        assert os.path.exists(tmp_path), "temp file should exist on success"
        with open(tmp_path, "rb") as f:
            assert f.read() == expected_bytes
        assert meta["save_id"] == 22, f"latest updated_at should win, got {meta['save_id']}"
        assert meta["file_name"] == "new.sav"
        assert meta["emulator"] == "snes9x"
        assert meta["romm_id"] == 555
        assert meta["size"] == len(expected_bytes)
        print("  ok")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


async def test_404_on_list():
    print("→ RomM returns 404 for the rom_id → RommNotFound")
    client = RomMClient("http://romm.test", "key")
    client.ensure_device_registered = AsyncMock(return_value=None)

    resp = AsyncMock()
    resp.status_code = 404
    resp.json = lambda: {}

    with patch("app.services.romm_client.httpx.AsyncClient") as mock_client_class:
        _setup_mock_client(mock_client_class, get_handler=lambda url, **_: resp)
        try:
            await client.pull_save_from_romm(999)
            raise AssertionError("expected RommNotFound")
        except RommNotFound:
            print("  ok")


async def test_empty_saves_list():
    print("→ RomM returns 200 but no saves → RommNotFound")
    client = RomMClient("http://romm.test", "key")
    client.ensure_device_registered = AsyncMock(return_value=None)

    resp = AsyncMock()
    resp.status_code = 200
    resp.json = lambda: []

    with patch("app.services.romm_client.httpx.AsyncClient") as mock_client_class:
        _setup_mock_client(mock_client_class, get_handler=lambda url, **_: resp)
        try:
            await client.pull_save_from_romm(1)
            raise AssertionError("expected RommNotFound")
        except RommNotFound:
            print("  ok")


async def test_5xx_on_list():
    print("→ RomM returns 500 listing saves → RommUpstreamError")
    client = RomMClient("http://romm.test", "key")
    client.ensure_device_registered = AsyncMock(return_value=None)

    resp = AsyncMock()
    resp.status_code = 502
    resp.json = lambda: {}

    with patch("app.services.romm_client.httpx.AsyncClient") as mock_client_class:
        _setup_mock_client(mock_client_class, get_handler=lambda url, **_: resp)
        try:
            await client.pull_save_from_romm(1)
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

    with patch("app.services.romm_client.httpx.AsyncClient") as mock_client_class:
        _setup_mock_client(
            mock_client_class,
            get_handler=lambda url, **_: list_resp,
            stream_routes=lambda url: (503, b""),
        )
        try:
            await client.pull_save_from_romm(1)
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

    stream_calls = []

    def stream_routes(url):
        stream_calls.append(url)
        if "/content/" in url:   # named path → 404
            return (404, b"")
        if url.endswith("/content"):  # fallback bare path → 200
            return (200, b"FALLBACK_OK")
        return (404, b"")

    with patch("app.services.romm_client.httpx.AsyncClient") as mock_client_class:
        _setup_mock_client(
            mock_client_class,
            get_handler=lambda url, **_: list_resp,
            stream_routes=stream_routes,
        )
        tmp_path, meta = await client.pull_save_from_romm(1)

    try:
        with open(tmp_path, "rb") as f:
            assert f.read() == b"FALLBACK_OK"
        assert any(u.endswith("/content/s.sav") for u in stream_calls), "should try named path first"
        assert any(u.endswith("/content") for u in stream_calls), "should fall back to bare /content"
        print("  ok")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


async def test_connect_error_maps_to_unavailable():
    print("→ httpx.ConnectError → RommUnavailable")
    client = RomMClient("http://romm.test", "key")
    client.ensure_device_registered = AsyncMock(return_value=None)

    async def raise_connect(url, **_):
        raise httpx.ConnectError("dns")

    with patch("app.services.romm_client.httpx.AsyncClient") as mock_client_class:
        _setup_mock_client(mock_client_class, get_handler=raise_connect)
        try:
            await client.pull_save_from_romm(1)
            raise AssertionError("expected RommUnavailable")
        except RommUnavailable:
            print("  ok")


async def test_timeout_maps_to_unavailable():
    print("→ httpx.TimeoutException → RommUnavailable")
    client = RomMClient("http://romm.test", "key")
    client.ensure_device_registered = AsyncMock(return_value=None)

    async def raise_timeout(url, **_):
        raise httpx.TimeoutException("slow")

    with patch("app.services.romm_client.httpx.AsyncClient") as mock_client_class:
        _setup_mock_client(mock_client_class, get_handler=raise_timeout)
        try:
            await client.pull_save_from_romm(1)
            raise AssertionError("expected RommUnavailable")
        except RommUnavailable:
            print("  ok")


async def test_unconfigured_client():
    print("→ client with no api_key → RommUnavailable (no network attempted)")
    client = RomMClient("http://romm.test", "")
    try:
        await client.pull_save_from_romm(1)
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

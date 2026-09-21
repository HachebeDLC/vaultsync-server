"""Coverage for the per-(base_url, api_key) RomMClient cache.

Before this cache existed, every per-user `RomMClient(url, key)` built in
app/routers/files.py and app/services/auto_sync_romm.py lazily created its
own httpx.AsyncClient in `_get_client()`, and nothing but the module-level
`romm_client` singleton was ever closed — leaking a connection pool per
request. `get_romm_client` reuses one instance per (base_url, api_key), and
`close_all_romm_clients` tears every cached instance down on shutdown.
"""
import pytest

from app.services import romm_client as romm_client_module
from app.services.romm_client import get_romm_client, close_all_romm_clients


@pytest.fixture(autouse=True)
async def _clean_cache():
    romm_client_module._client_cache.clear()
    yield
    await close_all_romm_clients()


async def test_same_creds_return_same_instance():
    a = get_romm_client("https://romm.example.com", "key-1")
    b = get_romm_client("https://romm.example.com", "key-1")
    assert a is b


async def test_different_creds_return_different_instances():
    same_url_diff_key = get_romm_client("https://romm.example.com", "key-1")
    diff_key = get_romm_client("https://romm.example.com", "key-2")
    diff_url = get_romm_client("https://other.example.com", "key-1")

    assert same_url_diff_key is not diff_key
    assert same_url_diff_key is not diff_url
    assert diff_key is not diff_url


async def test_trailing_slash_maps_to_same_instance():
    a = get_romm_client("https://romm.example.com", "key-1")
    b = get_romm_client("https://romm.example.com/", "key-1")
    assert a is b


async def test_close_all_closes_underlying_clients_and_clears_cache():
    a = get_romm_client("https://romm.example.com", "key-1")
    b = get_romm_client("https://other.example.com", "key-2")

    # Force creation of the underlying httpx.AsyncClient for each, the way a
    # real API call via heartbeat()/upload_save()/etc. would.
    httpx_a = await a._get_client()
    httpx_b = await b._get_client()
    assert not httpx_a.is_closed
    assert not httpx_b.is_closed

    assert len(romm_client_module._client_cache) == 2

    await close_all_romm_clients()

    assert httpx_a.is_closed
    assert httpx_b.is_closed
    assert romm_client_module._client_cache == {}

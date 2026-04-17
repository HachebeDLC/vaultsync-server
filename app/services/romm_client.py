import os
import socket
import tempfile
import httpx
import logging
from typing import Optional, Dict, List, Tuple
from ..config import (
    ROMM_URL,
    ROMM_API_KEY,
    VAULTSYNC_VERSION,
    ROMM_DEVICE_NAME,
    ROMM_DEVICE_API_MIN_VERSION,
)
from .. import crud

logger = logging.getLogger("VaultSync")


class RommError(Exception):
    """Base class for RomM client errors surfaced to callers."""


class RommNotFound(RommError):
    """RomM returned 404 for a rom_id, save_id, or listed no saves for the rom."""


class RommUpstreamError(RommError):
    """RomM returned a non-404 error status (5xx or unexpected 4xx)."""


class RommUnavailable(RommError):
    """RomM was unreachable (connect error, timeout, or client not configured)."""


def _version_at_least(current: Optional[str], minimum: str) -> bool:
    """Argosy-style version compare: split on '-' / '.', numeric tuples."""
    if not current:
        return False
    def _parts(v: str):
        return [int(x) for x in v.split("-")[0].split(".") if x.isdigit()]
    c, m = _parts(current), _parts(minimum)
    pad = max(len(c), len(m))
    c += [0] * (pad - len(c))
    m += [0] * (pad - len(m))
    return c >= m


class RomMClient:
    def __init__(self, base_url: str = ROMM_URL, api_key: str = ROMM_API_KEY):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self._version: Optional[str] = None

    async def heartbeat(self) -> Tuple[bool, Optional[str]]:
        """Calls /api/heartbeat; caches the server version string. Returns (ok, version)."""
        if not self.base_url:
            return False, None
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base_url}/api/heartbeat",
                    headers=self.headers,
                    timeout=10.0,
                )
                if resp.status_code != 200:
                    return False, None
                data = resp.json() or {}
                system = data.get("SYSTEM") or data.get("system") or {}
                version = system.get("VERSION") or system.get("version")
                self._version = version
                return True, version
        except Exception as e:
            logger.error(f"RomM heartbeat failed: {e}")
            return False, None

    def supports_device_api(self) -> bool:
        return _version_at_least(self._version, ROMM_DEVICE_API_MIN_VERSION)

    async def check_instance(self) -> tuple[bool, str]:
        """Verify reachability + credentials via /api/roms?limit=1. Returns (ok, message)."""
        if not self.base_url or not self.api_key:
            return False, "RomM URL or API key is not configured"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base_url}/api/roms",
                    params={"limit": 1, "offset": 0},
                    headers=self.headers,
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    return True, "RomM instance is reachable and API key is valid"
                elif resp.status_code == 401:
                    return False, "RomM authentication failed (401) — API key is invalid or expired"
                elif resp.status_code == 403:
                    return False, "RomM access forbidden (403) — key lacks required permissions"
                else:
                    return False, f"RomM returned unexpected status {resp.status_code} from {self.base_url}"
        except httpx.ConnectError:
            return False, f"Could not connect to RomM at {self.base_url} — host unreachable"
        except httpx.TimeoutException:
            return False, f"Connection to RomM at {self.base_url} timed out"
        except Exception as e:
            return False, f"Unexpected error checking RomM instance: {e}"

    async def fetch_entire_library(self) -> List[Dict]:
        """Fetches the user's entire ROM library from RomM to cache locally."""
        if not self.api_key:
            return []

        all_items: List[Dict] = []
        offset = 0
        limit = 1000

        async with httpx.AsyncClient() as client:
            while True:
                try:
                    resp = await client.get(
                        f"{self.base_url}/api/roms",
                        params={"limit": limit, "offset": offset},
                        headers=self.headers,
                        timeout=60.0,
                    )

                    if resp.status_code == 200:
                        data = resp.json()
                        items = data.get("items", [])
                        if not items:
                            break

                        all_items.extend(items)
                        offset += limit

                        if len(items) < limit:
                            break
                    else:
                        logger.error(f"RomM API Error: {resp.status_code} to {self.base_url}")
                        break

                except Exception as e:
                    logger.error(f"Failed to fetch RomM library: {str(e)}")
                    break

        return all_items

    async def register_device(
        self,
        name: str = ROMM_DEVICE_NAME,
        client_version: str = VAULTSYNC_VERSION,
        hostname: Optional[str] = None,
    ) -> Optional[str]:
        """POST /api/devices — registers a device and returns its device_id."""
        if not self.api_key:
            return None
        if hostname is None:
            try:
                hostname = socket.gethostname()
            except Exception:
                hostname = None
        body = {
            "name": name,
            "platform": "server",
            "client": "vaultsync",
            "client_version": client_version,
            "hostname": hostname,
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.base_url}/api/devices",
                    json=body,
                    headers=self.headers,
                    timeout=15.0,
                )
                if resp.status_code in (200, 201):
                    data = resp.json() or {}
                    return data.get("device_id") or data.get("id")
                logger.error(f"RomM device registration failed: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.error(f"RomM device registration error: {e}")
        return None

    async def ensure_device_registered(self, conn, user_id: int) -> Optional[str]:
        """Returns a valid device_id for this user, registering on first use or version bump.

        Caches the device_id in `users.romm_device_id`. Re-registers if the cached
        client_version does not match VAULTSYNC_VERSION.
        """
        if not self.supports_device_api():
            ok, _ = await self.heartbeat()
            if not ok or not self.supports_device_api():
                return None

        cached_id, cached_version = crud.get_user_romm_device(conn, user_id)
        if cached_id and cached_version == VAULTSYNC_VERSION:
            return cached_id

        device_id = await self.register_device()
        if device_id:
            crud.set_user_romm_device(conn, user_id, device_id, VAULTSYNC_VERSION)
            conn.commit()
            logger.info(f"Registered RomM device for user {user_id}: {device_id}")
        return device_id

    async def upload_save(
        self,
        rom_id: int,
        file_path: str,
        emulator: str,
        slot: Optional[str] = None,
        device_id: Optional[str] = None,
        overwrite: bool = False,
    ) -> bool:
        """POST /api/saves — upload a save file for `rom_id`.

        `emulator` is required by RomM. `device_id` and `overwrite` are only sent when
        the server supports the device-aware API (RomM ≥ 4.7.0).
        """
        if not self.api_key:
            return False
        try:
            filename = os.path.basename(file_path)
            params: Dict[str, object] = {"rom_id": rom_id, "emulator": emulator}
            if slot:
                params["slot"] = slot
            if device_id and self.supports_device_api():
                params["device_id"] = device_id
                params["overwrite"] = "true" if overwrite else "false"

            async with httpx.AsyncClient() as client:
                with open(file_path, "rb") as f:
                    files = {"saveFile": (filename, f, "application/octet-stream")}
                    resp = await client.post(
                        f"{self.base_url}/api/saves",
                        params=params,
                        files=files,
                        headers=self.headers,
                        timeout=60.0,
                    )
                    if resp.status_code in (200, 201):
                        return True
                    logger.error(
                        f"RomM upload_save failed: {resp.status_code} rom={rom_id} "
                        f"emulator={emulator} slot={slot} body={resp.text[:300]}"
                    )
                    return False
        except Exception as e:
            logger.error(f"RomM upload failed: {str(e)}")
        return False

    async def upload_state(
        self,
        rom_id: int,
        file_path: str,
        emulator: str,
        device_id: Optional[str] = None,
    ) -> bool:
        """POST /api/states — save-state uploads go to a separate endpoint in RomM."""
        if not self.api_key:
            return False
        try:
            filename = os.path.basename(file_path)
            params: Dict[str, object] = {"rom_id": rom_id, "emulator": emulator}
            if device_id and self.supports_device_api():
                params["device_id"] = device_id

            async with httpx.AsyncClient() as client:
                with open(file_path, "rb") as f:
                    files = {"stateFile": (filename, f, "application/octet-stream")}
                    resp = await client.post(
                        f"{self.base_url}/api/states",
                        params=params,
                        files=files,
                        headers=self.headers,
                        timeout=60.0,
                    )
                    if resp.status_code in (200, 201):
                        return True
                    logger.error(
                        f"RomM upload_state failed: {resp.status_code} rom={rom_id} "
                        f"emulator={emulator} body={resp.text[:300]}"
                    )
                    return False
        except Exception as e:
            logger.error(f"RomM state upload failed: {str(e)}")
        return False

    async def download_save(
        self,
        rom_id: int,
        dest_dir: str,
        device_id: Optional[str] = None,
    ) -> Optional[str]:
        """Downloads the latest save for a given ROM to dest_dir."""
        if not self.api_key:
            return None
        try:
            list_params: Dict[str, object] = {"rom_id": rom_id}
            if device_id and self.supports_device_api():
                list_params["device_id"] = device_id

            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base_url}/api/saves",
                    params=list_params,
                    headers=self.headers,
                    timeout=30.0,
                )
                if resp.status_code != 200:
                    logger.error(f"RomM failed to fetch saves for {rom_id}: {resp.status_code}")
                    return None

                saves = resp.json()
                if not saves:
                    logger.info(f"No saves found for RomM ID {rom_id}")
                    return None

                latest_save = sorted(saves, key=lambda x: x.get('updated_at', ''), reverse=True)[0]
                save_id = latest_save['id']
                file_name = (
                    latest_save.get('file_name')
                    or latest_save.get('filename')
                    or latest_save.get('name')
                    or "save.zip"
                )

                dl_params: Dict[str, object] = {}
                if device_id and self.supports_device_api():
                    dl_params["device_id"] = device_id

                dl_resp = await client.get(
                    f"{self.base_url}/api/saves/{save_id}/content/{file_name}",
                    headers=self.headers,
                    params=dl_params or None,
                    timeout=120.0,
                    follow_redirects=True,
                )
                if dl_resp.status_code == 404:
                    dl_resp = await client.get(
                        f"{self.base_url}/api/saves/{save_id}/content",
                        headers=self.headers,
                        params=dl_params or None,
                        timeout=120.0,
                        follow_redirects=True,
                    )

                if dl_resp.status_code == 200:
                    os.makedirs(dest_dir, exist_ok=True)
                    dest_path = os.path.join(dest_dir, file_name)
                    with open(dest_path, "wb") as f:
                        f.write(dl_resp.content)
                    return dest_path
                logger.error(f"Failed to download save content {save_id}: {dl_resp.status_code}")
        except Exception as e:
            logger.error(f"RomM download failed: {str(e)}")
        return None

    async def pull_save_from_romm(
        self,
        conn,
        romm_id: int,
        user_id: int,
    ) -> Tuple[str, Dict]:
        """Downloads the latest save for a RomM rom_id to a temp file.

        Returns `(tmp_path, metadata)`; caller owns `tmp_path` and must delete it.
        Raises `RommNotFound`, `RommUpstreamError`, or `RommUnavailable` on failure.
        """
        if not self.api_key or not self.base_url:
            raise RommUnavailable("RomM client is not configured")

        device_id = await self.ensure_device_registered(conn, user_id)

        list_params: Dict[str, object] = {"rom_id": romm_id}
        if device_id and self.supports_device_api():
            list_params["device_id"] = device_id

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base_url}/api/saves",
                    params=list_params,
                    headers=self.headers,
                    timeout=30.0,
                )
                if resp.status_code == 404:
                    raise RommNotFound(f"RomM rom_id={romm_id} not found")
                if resp.status_code != 200:
                    raise RommUpstreamError(
                        f"RomM returned {resp.status_code} listing saves for rom_id={romm_id}"
                    )

                saves = resp.json() or []
                if not saves:
                    raise RommNotFound(f"No saves available for RomM rom_id={romm_id}")

                latest = sorted(saves, key=lambda x: x.get("updated_at", ""), reverse=True)[0]
                save_id = latest["id"]
                file_name = (
                    latest.get("file_name")
                    or latest.get("filename")
                    or latest.get("name")
                    or "save.bin"
                )

                dl_params: Dict[str, object] = {}
                if device_id and self.supports_device_api():
                    dl_params["device_id"] = device_id

                dl_resp = await client.get(
                    f"{self.base_url}/api/saves/{save_id}/content/{file_name}",
                    headers=self.headers,
                    params=dl_params or None,
                    timeout=120.0,
                    follow_redirects=True,
                )
                if dl_resp.status_code == 404:
                    dl_resp = await client.get(
                        f"{self.base_url}/api/saves/{save_id}/content",
                        headers=self.headers,
                        params=dl_params or None,
                        timeout=120.0,
                        follow_redirects=True,
                    )
                if dl_resp.status_code == 404:
                    raise RommNotFound(f"RomM save_id={save_id} content not found")
                if dl_resp.status_code != 200:
                    raise RommUpstreamError(
                        f"RomM returned {dl_resp.status_code} downloading save_id={save_id}"
                    )

                fd, tmp_path = tempfile.mkstemp(prefix="romm_pull_", suffix=f"_{file_name}")
                try:
                    with os.fdopen(fd, "wb") as fout:
                        fout.write(dl_resp.content)
                except Exception:
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                    raise

                return tmp_path, {
                    "save_id": save_id,
                    "romm_id": romm_id,
                    "file_name": file_name,
                    "updated_at": latest.get("updated_at"),
                    "emulator": latest.get("emulator"),
                    "size": len(dl_resp.content),
                }
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            raise RommUnavailable(f"Could not reach RomM at {self.base_url}: {e}") from e


# Global default instance
romm_client = RomMClient()

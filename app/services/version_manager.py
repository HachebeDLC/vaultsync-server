import glob
import os
import shutil
import logging
import asyncio
from datetime import datetime
from threading import Lock
from typing import List, Set, Tuple
from ..config import STORAGE_DIR

logger = logging.getLogger("VaultSync")

class VersionManager:
    """
    Manages file versions for users, including creation, rotation, and restoration.
    Versions are stored in a hidden '.versions' directory within each user's storage root.

    Versioning is Syncthing-style: the existing server copy is snapshotted before the
    first upload fragment overwrites it, so every overwrite produces a restorable version.
    """
    def __init__(self, storage_root: str, max_versions: int = 5):
        self.storage_root = storage_root
        self.max_versions = max_versions
        self._pending: Set[Tuple[int, str]] = set()  # (user_id, path) mid-upload
        self._lock = Lock()

    def get_version_dir(self, user_id: int) -> str:
        """
        Returns the absolute path to the user's version directory, creating it if necessary.
        """
        version_directory = os.path.join(self.storage_root, str(user_id), ".versions")
        os.makedirs(version_directory, exist_ok=True)
        return version_directory

    def create_version(self, user_id: int, path: str, device_name: str):
        """
        Creates a new historical version of the file at the given path.
        """
        source_path = os.path.normpath(os.path.join(self.storage_root, str(user_id), path.lstrip("/\\")))
        if not os.path.exists(source_path):
            return
        
        version_directory = self.get_version_dir(user_id)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_name = path.replace("/", "_").replace("\\", "_").replace(" ", "_")
        version_filename = f"{safe_name}.~{timestamp}~{device_name}~"
        destination_path = os.path.join(version_directory, version_filename)
        
        try:
            shutil.copy2(source_path, destination_path)
            self._rotate(user_id, path)
            logger.info(f"📦 VERSIONED: {path} -> {version_filename}")
        except Exception as e:
            logger.error(f"❌ Version creation error for {path}: {e}")

    def _rotate(self, user_id: int, path: str):
        """
        Removes oldest versions if the maximum version count is exceeded for a specific file.
        """
        version_directory = self.get_version_dir(user_id)
        safe_name = path.replace("/", "_").replace("\\", "_").replace(" ", "_")
        pattern = os.path.join(version_directory, f"{safe_name}.~*")
        versions = sorted(glob.glob(pattern))

        while len(versions) > self.max_versions:
            file_to_remove = versions.pop(0)
            os.remove(file_to_remove)
            logger.info(f"🗑️ ROTATED: Removed old version {os.path.basename(file_to_remove)}")

    def list_versions(self, user_id: int, path: str) -> List[dict]:
        """
        Returns a list of all historical versions for a file, sorted by most recent first.
        """
        version_directory = self.get_version_dir(user_id)
        safe_name = path.replace("/", "_").replace("\\", "_").replace(" ", "_")
        prefix = f"{safe_name}.~"
        results = []
        
        if not os.path.exists(version_directory):
            return []

        pattern = os.path.join(version_directory, f"{safe_name}.~*")
        versions = sorted(glob.glob(pattern), reverse=True)
        for version_filename in [os.path.basename(v) for v in versions]:
            full_path = os.path.join(version_directory, version_filename)
            try:
                parts = version_filename.split("~")
                if len(parts) < 3:
                    raise ValueError("Malformed version filename")
                    
                results.append({
                    "version_id": version_filename,
                    "device_name": parts[2],
                    "updated_at": int(os.path.getmtime(full_path) * 1000),
                    "size": os.path.getsize(full_path)
                })
            except Exception as e:
                logger.warning(f"⚠️ Skipping malformed version entry {version_filename}: {e}")
                continue
        return results

    def restore_version(self, user_id: int, path: str, version_id: str):
        """
        Restores a specific version back to the original file path.
        """
        version_directory = self.get_version_dir(user_id)
        source_path = os.path.join(version_directory, version_id)
        destination_path = os.path.normpath(os.path.join(self.storage_root, str(user_id), path.lstrip("/\\")))

        if not os.path.exists(source_path):
            raise FileNotFoundError(f"Version {version_id} not found")

        shutil.copy2(source_path, destination_path)
        logger.info(f"⏪ RESTORED: {version_id} -> {path}")

    def begin_upload(self, user_id: int, path: str, device_name: str):
        """
        Called before the first upload fragment is written for an existing file.
        Snapshots the current server copy so it becomes restorable (Syncthing-style).
        Subsequent fragments for the same upload are no-ops.
        """
        key = (user_id, path)
        with self._lock:
            if key in self._pending:
                return
            self._pending.add(key)
        self.create_version(user_id, path, device_name)

    def complete_upload(self, user_id: int, path: str):
        """
        Called after finalize. Clears the pending marker so the next overwrite
        of this file will produce a new snapshot.
        """
        with self._lock:
            self._pending.discard((user_id, path))

version_manager = VersionManager(STORAGE_DIR)
VersionManager(STORAGE_DIR)

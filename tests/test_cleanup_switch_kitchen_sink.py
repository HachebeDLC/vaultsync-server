"""Regression tests for `cleanup_garbage._is_switch_kitchen_sink`.

The client's `SyncPathResolver.getCloudRelPath` flattens every Switch save to a
title-ID-first cloud path (`switch/<titleId>/...`). The garbage classifier must
treat that canonical form as legitimate — an earlier version assumed the only
"real" form was `switch/nand/user/save/<profile>/...` and deleted title-ID-first
saves, leaving an empty directory skeleton ("N directories, 0 files").
"""
import os
from unittest.mock import MagicMock

os.environ.setdefault("VAULTSYNC_SECRET", "dummy")
os.environ.setdefault("DB_PASS", "testpass")

import app.cleanup_garbage as cg  # noqa: E402
from app.cleanup_garbage import (  # noqa: E402
    CATEGORIES,
    _delete_row,
    _is_switch_kitchen_sink,
    _prune_empty_dirs,
)


def test_title_id_first_paths_are_not_garbage():
    # Exactly the layout from the user's `tree storage/1/switch` dump.
    keep = [
        "switch/0100000000010000/savegame",
        "switch/0100F2C0115B6000/slot_00/data.bin",
        "switch/01006A800016E000/save_data/mii/file",
        "switch/0100D0E00E51E000/HotlineMiami/save",
        "switch/01005EE0036EC000/tagame/savedata/dbe_production/x",
    ]
    for p in keep:
        assert not _is_switch_kitchen_sink(p), f"should keep title-ID-first save: {p}"


def test_legacy_and_profile_forms_are_not_garbage():
    assert not _is_switch_kitchen_sink(
        "switch/nand/user/save/0000000000000000/deadbeefcafef00ddeadbeefcafef00d/01006F8002326000/ac0.sav"
    )
    assert not _is_switch_kitchen_sink(
        "switch/deadbeefcafef00ddeadbeefcafef00d/01006F8002326000/ac0.sav"
    )


def test_real_garbage_still_flagged():
    garbage = [
        "switch/HotlineMiami/save.dat",          # bare game-name dump
        "switch/RXC1/data",                      # rom id / random folder
        "switch/02006F8002326000/x",             # title ID must start with 01
        "switch/0100F2C0115B6/x",                # too short to be a title ID
    ]
    for p in garbage:
        assert _is_switch_kitchen_sink(p), f"should flag garbage: {p}"


def test_switch_matcher_is_never_registered_for_destructive_cleanup():
    assert 'switch_kitchen_sink' not in {category.key for category in CATEGORIES}


def test_delete_row_quarantines_blob_before_removing_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(cg, "STORAGE_DIR", str(tmp_path))
    source = tmp_path / "1" / "psp" / "stray.sav"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"recoverable")
    cursor = MagicMock()

    _delete_row(cursor, 1, 42, "psp/stray.sav", apply=True)

    assert not source.exists()
    quarantined = list((tmp_path / "1" / ".cleanup_trash").rglob("stray.sav"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"recoverable"
    cursor.execute.assert_called_once_with("DELETE FROM files WHERE id = %s", (42,))


def test_delete_row_keeps_metadata_when_quarantine_fails(monkeypatch):
    monkeypatch.setattr(cg, "_quarantine_physical", MagicMock(side_effect=OSError("disk error")))
    cursor = MagicMock()

    _delete_row(cursor, 1, 42, "switch/0100F2C0115B6000/save.bin", apply=True)

    cursor.execute.assert_not_called()


def test_delete_row_keeps_metadata_when_source_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cg, "STORAGE_DIR", str(tmp_path))
    cursor = MagicMock()

    _delete_row(cursor, 1, 42, "switch/0100F2C0115B6000/save.bin", apply=True)

    cursor.execute.assert_not_called()


def test_prune_empty_dirs_walks_up_to_user_root(tmp_path, monkeypatch):
    monkeypatch.setattr(cg, "STORAGE_DIR", str(tmp_path))
    user_root = tmp_path / "1"
    deep = user_root / "switch" / "0100F2C0115B6000" / "slot_00"
    deep.mkdir(parents=True)
    # File already unlinked by _delete_row; only the empty skeleton remains.
    _prune_empty_dirs(1, "switch/0100F2C0115B6000/slot_00/data.bin")
    assert not (user_root / "switch").exists(), "empty switch skeleton should be pruned"
    assert user_root.exists(), "user storage root must never be removed"


def test_prune_stops_at_non_empty_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cg, "STORAGE_DIR", str(tmp_path))
    user_root = tmp_path / "1"
    title_dir = user_root / "switch" / "0100F2C0115B6000"
    (title_dir / "slot_00").mkdir(parents=True)
    keep = title_dir / "slot_01" / "keep.bin"
    keep.parent.mkdir(parents=True)
    keep.write_text("x")
    _prune_empty_dirs(1, "switch/0100F2C0115B6000/slot_00/data.bin")
    assert not (title_dir / "slot_00").exists(), "empty sibling should be pruned"
    assert keep.exists(), "non-empty branch must be preserved"
    assert title_dir.exists(), "title dir with a surviving child must remain"

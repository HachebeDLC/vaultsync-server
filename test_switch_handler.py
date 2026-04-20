"""Unit tests for `romm_sync_test.SwitchHandler`.

Covers the Argosy-parity title-ID validator, device-save set, and the path
scanner that replaced the old `parts[1]` indexing.
"""
import os

# Must be set before importing romm_sync_test — its module-level code pulls in
# `app.config`, which hard-errors if the secret is missing.
os.environ.setdefault("VAULTSYNC_SECRET", "dummy")

from romm_sync_test import SwitchHandler  # noqa: E402


def test_is_valid_title_id():
    print("→ is_valid_title_id")
    # Happy path — 01 + 14 hex
    assert SwitchHandler.is_valid_title_id("01006F8002326000")
    assert SwitchHandler.is_valid_title_id("0101000000000000")
    # Case-insensitive hex
    assert SwitchHandler.is_valid_title_id("01AbCdEf00000000")
    # Wrong prefix
    assert not SwitchHandler.is_valid_title_id("02006F8002326000")
    # Wrong length
    assert not SwitchHandler.is_valid_title_id("01006F80")
    assert not SwitchHandler.is_valid_title_id("01006F80023260000")
    # Non-hex
    assert not SwitchHandler.is_valid_title_id("01006F800232600G")
    # Empty / None-ish
    assert not SwitchHandler.is_valid_title_id("")
    # Regular path segment names must NOT validate — this is the bug the old
    # `parts[1]` indexer produced (reporting `nand` as the title ID).
    assert not SwitchHandler.is_valid_title_id("nand")
    assert not SwitchHandler.is_valid_title_id("user")
    assert not SwitchHandler.is_valid_title_id("save")
    print("  ok")


def test_is_device_save():
    print("→ is_device_save (9 hardcoded title IDs)")
    assert SwitchHandler.is_device_save("01006F8002326000"), "ACNH"
    assert SwitchHandler.is_device_save("01002FF008C24000"), "Ring Fit"
    # Case-insensitive
    assert SwitchHandler.is_device_save("01006f8002326000")
    # Profile-bound titles are NOT device saves
    assert not SwitchHandler.is_device_save("0100000000010000")  # Mario Odyssey-ish
    assert not SwitchHandler.is_device_save("")
    print("  ok")


def test_extract_meta_deep_nand_path():
    print("→ extract_meta: deep nand path with active profile")
    h = SwitchHandler()
    path = "switch/nand/user/save/0000000000000000/deadbeefcafef00ddeadbeefcafef00d/01006F8002326000/Account/ac0.sav"
    gk, tid, fuzzy, inner = h.extract_meta("switch", path)
    assert tid == "01006F8002326000", f"expected ACNH titleId, got {tid!r}"
    assert gk == "switch:01006F8002326000"
    assert fuzzy is None
    assert inner == "Account/ac0.sav", inner
    print("  ok")


def test_extract_meta_device_save_path():
    print("→ extract_meta: device save under zero-user/zero-profile")
    h = SwitchHandler()
    path = "switch/nand/user/save/0000000000000000/00000000000000000000000000000000/01002FF008C24000/save.dat"
    gk, tid, fuzzy, inner = h.extract_meta("switch", path)
    assert tid == "01002FF008C24000"
    assert gk == "switch:01002FF008C24000"
    assert inner == "save.dat"
    print("  ok")


def test_extract_meta_junk_path_no_valid_titleid():
    print("→ extract_meta: path without valid titleId does NOT collapse into switch:nand")
    h = SwitchHandler()
    # Previously: `parts[1]` = "nand" → group key `switch:nand`, which poisoned
    # every unmatched save into one bucket. Now: should fall through to a
    # filename-scoped group so stray files don't pollute real groups.
    gk, tid, fuzzy, inner = h.extract_meta("switch", "switch/nand/user/save/something.sav")
    assert tid is None, f"junk paths should yield no titleId, got {tid!r}"
    assert not gk.startswith("switch:nand"), f"regression: junk path grouped as {gk!r}"
    assert gk.startswith("switch:?")
    assert fuzzy == "something"  # clean_game_name strips .sav
    print("  ok")


def test_extract_meta_shallow_path():
    print("→ extract_meta: shallow root (legacy layout)")
    h = SwitchHandler()
    gk, tid, fuzzy, inner = h.extract_meta("switch", "switch/01006F8002326000/save.bin")
    assert tid == "01006F8002326000"
    assert gk == "switch:01006F8002326000"
    assert inner == "save.bin"
    print("  ok")


def test_extract_meta_uppercases_titleid():
    print("→ extract_meta: title IDs normalized to uppercase")
    h = SwitchHandler()
    _, tid, _, _ = h.extract_meta(
        "switch",
        "switch/nand/user/save/0000000000000000/deadbeefcafef00ddeadbeefcafef00d/01006f8002326000/save.bin",
    )
    assert tid == "01006F8002326000", tid
    print("  ok")


def test_get_emulator():
    print("→ get_emulator returns 'eden' regardless of platform")
    h = SwitchHandler()
    assert h.get_emulator("switch") == "eden"
    assert h.get_emulator("eden") == "eden"
    print("  ok")


def test_can_handle():
    print("→ can_handle: switch and eden, nothing else")
    h = SwitchHandler()
    assert h.can_handle("switch", "anything")
    assert h.can_handle("eden", "anything")
    assert not h.can_handle("3ds", "anything")
    assert not h.can_handle("retroarch", "anything")
    print("  ok")


def main():
    test_is_valid_title_id()
    test_is_device_save()
    test_extract_meta_deep_nand_path()
    test_extract_meta_device_save_path()
    test_extract_meta_junk_path_no_valid_titleid()
    test_extract_meta_shallow_path()
    test_extract_meta_uppercases_titleid()
    test_get_emulator()
    test_can_handle()
    print("\nAll SwitchHandler tests passed.")


if __name__ == "__main__":
    main()

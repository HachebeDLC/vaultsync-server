"""Unit tests for `romm_sync_test.load_env`.

Specifically guards the "empty .env value must not clobber a pre-set env var"
rule, which was previously wiping `VAULTSYNC_SECRET` during test runs.
"""
import os
import tempfile

os.environ.setdefault("VAULTSYNC_SECRET", "dummy")

from romm_sync_test import load_env  # noqa: E402


def _write_env(contents: str) -> str:
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".env", delete=False, encoding="utf-8"
    )
    f.write(contents)
    f.close()
    return f.name


def test_empty_value_preserves_existing_env_var():
    print("→ empty .env value must NOT clobber an already-set env var")
    os.environ["TEST_LOAD_ENV_VAR"] = "preset-value"
    path = _write_env("TEST_LOAD_ENV_VAR=\n")
    try:
        load_env(path)
        assert os.environ["TEST_LOAD_ENV_VAR"] == "preset-value", (
            f"expected 'preset-value', got {os.environ['TEST_LOAD_ENV_VAR']!r}"
        )
    finally:
        os.unlink(path)
        del os.environ["TEST_LOAD_ENV_VAR"]
    print("  ok")


def test_non_empty_value_overwrites_existing_env_var():
    print("→ non-empty .env value overwrites pre-set env var (expected)")
    os.environ["TEST_LOAD_ENV_VAR"] = "preset-value"
    path = _write_env("TEST_LOAD_ENV_VAR=from-file\n")
    try:
        load_env(path)
        assert os.environ["TEST_LOAD_ENV_VAR"] == "from-file"
    finally:
        os.unlink(path)
        del os.environ["TEST_LOAD_ENV_VAR"]
    print("  ok")


def test_new_empty_value_still_sets_empty_string():
    print("→ empty .env value on a NEW key sets empty string")
    if "TEST_LOAD_ENV_VAR" in os.environ:
        del os.environ["TEST_LOAD_ENV_VAR"]
    path = _write_env("TEST_LOAD_ENV_VAR=\n")
    try:
        load_env(path)
        assert os.environ.get("TEST_LOAD_ENV_VAR") == ""
    finally:
        os.unlink(path)
        os.environ.pop("TEST_LOAD_ENV_VAR", None)
    print("  ok")


def test_comments_and_blank_lines_ignored():
    print("→ '#' comments and blank lines are skipped")
    os.environ.pop("TEST_LOAD_ENV_VAR", None)
    path = _write_env("\n# a comment\nTEST_LOAD_ENV_VAR=val\n\n")
    try:
        load_env(path)
        assert os.environ["TEST_LOAD_ENV_VAR"] == "val"
    finally:
        os.unlink(path)
        del os.environ["TEST_LOAD_ENV_VAR"]
    print("  ok")


def test_strips_surrounding_quotes():
    print("→ surrounding single/double quotes are stripped")
    os.environ.pop("TEST_LOAD_ENV_VAR", None)
    path = _write_env('TEST_LOAD_ENV_VAR="quoted"\n')
    try:
        load_env(path)
        assert os.environ["TEST_LOAD_ENV_VAR"] == "quoted"
    finally:
        os.unlink(path)
        del os.environ["TEST_LOAD_ENV_VAR"]
    print("  ok")


def test_missing_file_is_noop():
    print("→ missing .env file is a silent no-op")
    before = dict(os.environ)
    load_env("/tmp/definitely-does-not-exist-vaultsync.env")
    assert dict(os.environ) == before
    print("  ok")


def main():
    test_empty_value_preserves_existing_env_var()
    test_non_empty_value_overwrites_existing_env_var()
    test_new_empty_value_still_sets_empty_string()
    test_comments_and_blank_lines_ignored()
    test_strips_surrounding_quotes()
    test_missing_file_is_noop()
    print("\nAll load_env tests passed.")


if __name__ == "__main__":
    main()

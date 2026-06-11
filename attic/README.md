# attic/

One-off diagnostic, migration, and manual-verification scripts kept out of the
repo root. Nothing here is imported by the server at runtime; one test
(`tests/test_load_env.py`) imports `load_env` from `romm_sync_test.py`.

Run them from the **repo root** so `import app` resolves:

```bash
cd vaultsync_server
python attic/check_db.py
```

| File | Purpose |
|---|---|
| `check_db.py` | Inspect users/files tables in the live DB |
| `check_server_state.py` | Dump server-side sync state for a user |
| `cleanup_switch.py` | Purge stale Switch save entries |
| `derive_zk_key.py` | Derive a ZK master key from email+password (debug) |
| `local_match_test.py` | Try RomM title-matching against the local DB |
| `migration_fix.sh` | Historical schema migration patch |
| `romm_sync_test.py` | Manual end-to-end RomM sync exercise (also exports `load_env`) |
| `romm_tree_matcher.py` | Match save paths against a RomM library tree |
| `simulate_romm_match.py` | Simulate RomM title matching via httpx |
| `upgrade_remote.sh` | Historical remote-server upgrade script |
| `verify_sync.py` | Decrypt and verify a synced file end-to-end |
| `vibedrift-report.html` | Old generated analysis report |
| `pc_verified_Mcd002.ps2` | Reference PS2 memory card image used for manual verification (gitignored) |

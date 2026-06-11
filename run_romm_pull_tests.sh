#!/usr/bin/env bash
# Runs the three test layers covering /api/v1/romm/pull:
#   1. RomMClient.pull_save_from_romm unit coverage    (tests/test_romm_pull.py)
#   2. /api/v1/romm/pull endpoint coverage             (tests/test_romm_pull_endpoint.py)
#   3. End-to-end round-trip vs. mock RomM (httpx ASGI) (tests/test_romm_roundtrip.py)
set -euo pipefail

cd "$(dirname "$0")"

export VAULTSYNC_SECRET="${VAULTSYNC_SECRET:-dummy}"
export DB_PASS="${DB_PASS:-dummy}"

python -m pytest \
    tests/test_romm_pull.py \
    tests/test_romm_pull_endpoint.py \
    tests/test_romm_roundtrip.py \
    --asyncio-mode=auto -v

echo
echo "All RomM pull test layers passed."

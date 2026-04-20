#!/usr/bin/env bash
# Runs the three test layers covering /api/v1/romm/pull:
#   1. RomMClient.pull_save_from_romm unit coverage    (test_romm_pull.py)
#   2. /api/v1/romm/pull endpoint coverage             (test_romm_pull_endpoint.py)
#   3. End-to-end round-trip vs. mock RomM (httpx ASGI) (test_romm_roundtrip.py)
set -euo pipefail

cd "$(dirname "$0")"

export VAULTSYNC_SECRET="${VAULTSYNC_SECRET:-dummy}"

echo "=== 1/3: pull_save_from_romm unit ==="
python test_romm_pull.py

echo
echo "=== 2/3: /api/v1/romm/pull endpoint ==="
python test_romm_pull_endpoint.py

echo
echo "=== 3/3: vaultsync ↔ mock RomM round-trip (byte-parity) ==="
python test_romm_roundtrip.py

echo
echo "All RomM pull test layers passed."

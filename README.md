# VaultSync Server

RAM-safe, bit-perfect backend for the VaultSync synchronization platform.

## Features
- **FastAPI Core:** High-performance async Python backend.
- **Zero-Copy Patching:** Uses `f.seek()` for direct binary block updates, keeping memory usage near-constant.
- **PostgreSQL Meta-Sync:** Reliable file tracking and block manifest management.
- **Streaming Restoration:** Native `FileResponse` support for high-speed save downloads.
- **Cloudflare Compatible:** Standardized on Port 8080 for seamless proxying.

## Setup
VaultSync is designed to run in Docker for maximum reliability.

```bash
cp .env.example .env   # fill in VAULTSYNC_SECRET, DB_PASS, POSTGRES_PASSWORD
docker compose up --build -d
```

## Security
VaultSync is a Zero-Knowledge system. The server stores hardware-encrypted fragments (`AES-256-CBC`) and has no access to your local Master Key.

**Exception:** if the optional RomM integration is enabled, the server temporarily
decrypts saves in order to push them to RomM. The app shows a disclosure dialog
before letting users enable it.

### Rate limiting behind a reverse proxy
Login, register, and recovery endpoints are rate-limited with `slowapi`, keyed on
the client IP (`get_remote_address`). If you run VaultSync behind a reverse proxy
or Cloudflare, the server sees the proxy's IP for every request, so **all clients
share one rate-limit bucket**. Configure your proxy to forward the real client IP
and enforce per-client limits at the proxy layer (e.g. nginx `limit_req`,
Cloudflare rate-limiting rules), or run uvicorn with `--proxy-headers` and a
trusted `--forwarded-allow-ips` so `X-Forwarded-For` is honored.

## Tests
Unit and offline-integration tests live in `tests/`:

```bash
python -m pytest          # uses pytest.ini (testpaths=tests, asyncio auto)
./run_romm_pull_tests.sh  # just the RomM pull layers
```

`tests/test_api_endpoints.py`, `tests/test_refresh_tokens.py` and
`tests/test_sse_broadcast.py` expect a **running server** (see `TEST_BASE_URL`);
`tests/test_db_schema.py` expects a reachable Postgres. CI
(`.github/workflows/server-tests.yml`) runs everything except the
live-server tests, with Postgres and Redis as services.

## Verification
Use the archived `attic/verify_sync.py` script to verify bit-perfect integrity from your PC:
```bash
python3 attic/verify_sync.py <BASE_URL> <EMAIL> <PASSWORD> <REMOTE_PATH>
```

One-off diagnostic and migration scripts live in [`attic/`](attic/README.md).

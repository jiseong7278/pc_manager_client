# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Install test dependencies
pip install -r requirements-test.txt

# Run all tests
pytest tests/

# Run a single test file
pytest tests/test_updater.py

# Run debug mode (no Windows service, for development)
python main.py debug

# Build exe (requires PyInstaller)
pyinstaller build.spec
# Output: dist/PCInspectClient/PCInspectClient.exe

# Generate WiX installer component file (after build)
python generate_wxs.py
```

## Architecture

This is a **Windows-only** PC management client that runs as a Windows service. It collects PC hardware/security data and communicates with a server through Redis and WebSocket.

### Data Flow

```
Server → Redis Pub-Sub (pc_inspect channel) → Client receives command
Client → collect PC data → Redis Stream (pc_reports key) → Server
Client → WebSocket (/ws/client) → 60s heartbeat → Server
```

**Commands received via Pub-Sub:**
- `inspect` — collect hardware/antivirus data and publish to the Redis Stream
- `update` — immediately check GitHub Releases and self-update via MSI
- `set_token` — store GitHub token to registry (DPAPI-encrypted)
- `set_secret` — store HMAC secret to registry (DPAPI-encrypted); exempt from HMAC verification
- `av_update` — trigger antivirus update via allowed path list

Each message payload is JSON: `{"command": "inspect", "target": "PC-001", "sig": "hmac..."}`. The `target`/`targets` field is optional; when present, only the matching hostname processes the command. The `sig` field is HMAC-SHA256 of the payload (excluding `sig` itself, keys sorted alphabetically).

### Module Responsibilities

- **`main.py`** — Entry point. Routes CLI args to service management (install/start/stop/remove/status) or `debug` mode. No-arg execution auto-installs and starts the service.
- **`service.py`** — Defines `PCInspectService` (pywin32 `ServiceFramework`). Starts three daemon threads: `RedisSubscriber`, `AutoUpdater`, and heartbeat WebSocket sender. Logs to `logs/client.log` with daily rotation (30-day retention).
- **`redis_client.py`** — Two responsibilities:
  1. **Pub-Sub subscriber**: Loops on `pc_inspect` channel with exponential backoff reconnect (5s→10s→30s→60s→120s). On command, verifies HMAC signature (registry secret), filters by `target`/`targets`, then dispatches.
  2. **Heartbeat sender**: Connects to `/ws/client` via WSS with `truststore` SSL context, sends 60s heartbeat `{"type":"heartbeat","hostname":"...","version":"...","device_type":"pc"}`. Same exponential backoff reconnect.
  3. **Failed report cache**: If `XADD` to Redis Stream fails, saves to `failed_reports.json` and retries on next successful connection.
- **`collector.py`** — Collects hardware (MAC, hostname, OS, CPU, GPU, RAM, disks) and antivirus info (Windows Defender via WMI, V3 and 알약 via registry) using PowerShell subprocess. All data paths have try/except fallbacks; errors go in `collection_errors`. Antivirus update paths validated via `_is_allowed_av_path()` (checks `_ALLOWED_AV_DIRS` + dynamic registry discovery via `_find_av_install_dir()`).
- **`updater.py`** — Polls GitHub Releases API every `UPDATE_CHECK_INTERVAL` seconds (default 300s). If a newer release has a `.msi` asset, downloads to temp, verifies SHA-256 hash against `digest` field, runs `msiexec /i /qn` for silent install. GitHub token read from registry (DPAPI-decrypted). Only runs when frozen as a PyInstaller exe.
- **`config.py`** — Central configuration. `REDIS_HOST` and `SERVER_WS_URL` are injected at CI/CD build time from `secrets.SERVER_REDIS_HOST`. `GITHUB_REPO` must be set before build.

### Security

- **HMAC verification**: `sig = HMAC-SHA256(HMAC_SECRET, compact JSON with sorted keys, excluding "sig")`. Commands without a valid sig are dropped (logged as warning). `set_secret` and `set_token` are exempt.
- **DPAPI encryption**: `GitHubToken` and `HMACSecret` stored in `HKLM\SOFTWARE\PCInspector\` as `REG_BINARY` encrypted with `CryptProtectData(CRYPTPROTECT_LOCAL_MACHINE)`. Legacy plaintext `REG_SZ` values are auto-read with fallback.
- **TLS**: WebSocket uses `truststore.SSLContext` only when URL scheme is `wss://`. Plain `ws://` skips SSL context.
- **PowerShell injection protection**: V3 update path uses `-LiteralPath` and single-quoted strings to prevent variable substitution.

### Testing Notes

Tests live in `tests/` and use `unittest.mock` to mock Windows-specific modules (`win32serviceutil`, `win32service`, `win32event`, `servicemanager`, `redis`, `win32crypt`) so they run without a Windows service environment. Tests cover: HMAC verification, target filtering, MSI asset finding, SHA-256 validation, DPAPI mock, version comparison.

### Build Notes

`build.spec` uses PyInstaller in **onedir mode** (not onefile). Output is `dist/PCInspectClient/` with `PCInspectClient.exe` as the launcher. `generate_wxs.py` generates a WiX component file (`dist_files.wxs`) from the build output. The WiX `installer.wxs` bundles `ca.crt` and registers it in the Windows Root CA store on install (`certutil -addstore -f Root ca.crt`), enabling the client to trust the server's self-signed certificate.

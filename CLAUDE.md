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

This is a **Windows-only** PC management client that runs as a Windows service. It collects PC hardware/security data and communicates with a server through Redis.

### Data Flow

```
Server → Redis Pub-Sub (pc_inspect channel) → Client receives command
Client → collect PC data → Redis Stream (pc_reports key) → Server
```

**Commands received via Pub-Sub:**
- `inspect` — collect hardware/antivirus data and publish to the stream
- `update` — immediately check GitHub Releases and self-update

Each message payload is JSON: `{"command": "inspect", "target": "PC-001"}`. The `target` field is optional; when present, only the matching hostname processes the command.

### Module Responsibilities

- **`main.py`** — Entry point. Routes CLI args to service management (install/start/stop/remove/status) or `debug` mode. No-arg execution auto-installs and starts the service.
- **`service.py`** — Defines `PCInspectService` (pywin32 `ServiceFramework`). Starts two daemon threads: `RedisSubscriber` and `AutoUpdater`. Logs to `logs/client.log` with daily rotation.
- **`redis_client.py`** — Subscribes to the Redis channel in a loop with automatic reconnect on failure (5s retry). On `inspect` command, calls `collector.collect_all()`, appends `hostname`/`ip_address`, then `xadd`s to the stream.
- **`collector.py`** — Collects hardware (MAC, hostname, OS, CPU, GPU, RAM, disks) and antivirus info (Windows Defender, V3, 알약) using PowerShell via `subprocess`. All data paths have try/except fallbacks.
- **`updater.py`** — Polls GitHub Releases API every `UPDATE_CHECK_INTERVAL` seconds (default 1 hour). If a newer version is found with an `.exe` asset, downloads it to `%TEMP%` and runs a `.bat` script that replaces the running exe and restarts the service. Only works when running as a frozen exe.
- **`config.py`** — Central configuration. **Must be edited** before deployment: set `REDIS_HOST` and `GITHUB_REPO`.

### Testing Notes

Tests live in `tests/` and use `unittest.mock` to mock Windows-specific modules (`win32serviceutil`, `win32service`, `win32event`, `servicemanager`, `redis`) so they can run without a Windows service environment. Tests cover pure logic only (version parsing, command parsing, asset finding).

### Build Notes

`build.spec` uses PyInstaller in **onedir mode** (not onefile). Output is `dist/PCInspectClient/` with `PCInspectClient.exe` as the launcher. `config.py` is explicitly included as a data file so it can be edited after build. `generate_wxs.py` generates a WiX component file (`dist_files.wxs`) from the build output for MSI packaging.

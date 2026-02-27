"""tests/test_updater.py - updater 순수 함수 테스트 (Windows 의존성 없음)"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock

# Windows 전용 모듈 mock 처리 (GitHub Actions Windows runner에 없을 수 있음)
sys.modules.setdefault("win32serviceutil", MagicMock())
sys.modules.setdefault("win32service",     MagicMock())
sys.modules.setdefault("win32event",       MagicMock())
sys.modules.setdefault("servicemanager",   MagicMock())

from updater import _parse_version, _is_newer, _find_exe_asset


class TestParseVersion:

    def test_strips_v_prefix(self):
        assert _parse_version("v1.2.3") == "1.2.3"

    def test_no_prefix(self):
        assert _parse_version("1.2.3") == "1.2.3"

    def test_major_only(self):
        assert _parse_version("v2") == "2"

    def test_empty_string(self):
        assert _parse_version("") == ""


class TestIsNewer:

    def test_newer_patch(self):
        assert _is_newer("1.0.1", "1.0.0") is True

    def test_newer_minor(self):
        assert _is_newer("1.1.0", "1.0.9") is True

    def test_newer_major(self):
        assert _is_newer("2.0.0", "1.9.9") is True

    def test_same_version(self):
        assert _is_newer("1.0.0", "1.0.0") is False

    def test_older_version(self):
        assert _is_newer("1.0.0", "1.0.1") is False

    def test_invalid_version_returns_false(self):
        assert _is_newer("invalid", "1.0.0") is False

    def test_empty_version_returns_false(self):
        assert _is_newer("", "1.0.0") is False


class TestFindExeAsset:

    def test_finds_exe(self):
        assets = [
            {"name": "PCInspectClient.exe", "browser_download_url": "https://example.com/v1.exe"},
            {"name": "checksums.txt",        "browser_download_url": "https://example.com/checksums.txt"},
        ]
        result = _find_exe_asset(assets)
        assert result is not None
        assert result["name"] == "PCInspectClient.exe"

    def test_returns_none_when_no_exe(self):
        assets = [
            {"name": "README.md",      "browser_download_url": "https://example.com/README.md"},
            {"name": "checksums.txt",  "browser_download_url": "https://example.com/checksums.txt"},
        ]
        assert _find_exe_asset(assets) is None

    def test_empty_assets(self):
        assert _find_exe_asset([]) is None

    def test_picks_first_exe(self):
        assets = [
            {"name": "PCInspectClient_x86.exe", "browser_download_url": "https://example.com/x86.exe"},
            {"name": "PCInspectClient_x64.exe", "browser_download_url": "https://example.com/x64.exe"},
        ]
        result = _find_exe_asset(assets)
        assert result["name"] == "PCInspectClient_x86.exe"

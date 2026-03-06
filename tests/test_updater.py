"""tests/test_updater.py - updater 순수 함수 테스트 (Windows 의존성 없음)"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock

# Windows 전용 및 SSL 관련 모듈 mock 처리
sys.modules.setdefault("win32serviceutil", MagicMock())
sys.modules.setdefault("win32service",     MagicMock())
sys.modules.setdefault("win32event",       MagicMock())
sys.modules.setdefault("servicemanager",   MagicMock())
sys.modules.setdefault("truststore",       MagicMock())

from updater import _parse_version, _is_newer, _find_msi_asset, _verify_sha256


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


class TestFindMsiAsset:

    def test_finds_msi(self):
        assets = [
            {"name": "PCInspectClient_1.0.0.msi", "browser_download_url": "https://example.com/v1.msi"},
            {"name": "checksums.txt",              "browser_download_url": "https://example.com/checksums.txt"},
        ]
        result = _find_msi_asset(assets)
        assert result is not None
        assert result["name"] == "PCInspectClient_1.0.0.msi"

    def test_returns_none_when_no_msi(self):
        assets = [
            {"name": "README.md",      "browser_download_url": "https://example.com/README.md"},
            {"name": "checksums.txt",  "browser_download_url": "https://example.com/checksums.txt"},
        ]
        assert _find_msi_asset(assets) is None

    def test_empty_assets(self):
        assert _find_msi_asset([]) is None

    def test_picks_first_msi(self):
        assets = [
            {"name": "PCInspectClient_1.0.0.msi", "browser_download_url": "https://example.com/v1.msi"},
            {"name": "PCInspectClient_1.0.1.msi", "browser_download_url": "https://example.com/v2.msi"},
        ]
        result = _find_msi_asset(assets)
        assert result["name"] == "PCInspectClient_1.0.0.msi"


class TestVerifySha256:

    def test_valid_digest_returns_true(self, tmp_path):
        import hashlib
        content  = b"test msi content"
        hex_hash = hashlib.sha256(content).hexdigest()

        msi_file = tmp_path / "test.msi"
        msi_file.write_bytes(content)

        result = _verify_sha256(str(msi_file), f"sha256:{hex_hash}")
        assert result is True

    def test_invalid_digest_returns_false(self, tmp_path):
        msi_file = tmp_path / "test.msi"
        msi_file.write_bytes(b"test msi content")

        result = _verify_sha256(str(msi_file), f"sha256:{'0' * 64}")
        assert result is False

    def test_unknown_digest_format_skips_verification(self, tmp_path):
        """sha256: 이외 형식은 검증 건너뜀 (True 반환)"""
        msi_file = tmp_path / "test.msi"
        msi_file.write_bytes(b"content")

        result = _verify_sha256(str(msi_file), "md5:abc123")
        assert result is True

    def test_file_read_error_returns_false(self, tmp_path):
        result = _verify_sha256("/nonexistent/path/test.msi", "sha256:" + "a" * 64)
        assert result is False


class TestUpdateLock:

    def test_trigger_update_skipped_when_lock_held(self):
        """_update_lock 보유 중에는 trigger_update가 조기 반환 (외부 요청 없음)"""
        import updater
        with updater._update_lock:
            with patch("updater._get_latest_release") as mock_release:
                updater.trigger_update()
                mock_release.assert_not_called()

    def test_trigger_update_runs_when_lock_free(self):
        """락이 비어 있으면 정상 실행되고 락이 해제됨"""
        import updater
        with patch("updater._get_latest_release", return_value=None):
            updater.trigger_update()
        # 실행 후 락이 해제된 상태여야 함
        assert updater._update_lock.acquire(blocking=False)
        updater._update_lock.release()

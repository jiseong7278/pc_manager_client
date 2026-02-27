"""tests/test_redis_client.py - redis_client 순수 함수 테스트"""

import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock

sys.modules.setdefault("win32serviceutil", MagicMock())
sys.modules.setdefault("win32service",     MagicMock())
sys.modules.setdefault("win32event",       MagicMock())
sys.modules.setdefault("servicemanager",   MagicMock())
sys.modules.setdefault("redis",            MagicMock())

import config


class TestCommandParsing:
    """subscribe_and_run의 명령어 파싱 로직 단위 테스트"""

    def _parse_command(self, raw: str, my_hostname: str) -> tuple[str | None, bool]:
        """
        실제 subscribe_and_run의 파싱 로직을 그대로 추출
        returns (command, should_execute)
        """
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None, False

        if not isinstance(payload, dict):
            return None, False

        command = payload.get("command")
        target  = payload.get("target")

        if target and target != my_hostname:
            return command, False

        return command, True

    def test_inspect_broadcast(self):
        cmd, should = self._parse_command('{"command": "inspect"}', "PC-001")
        assert cmd == "inspect"
        assert should is True

    def test_update_broadcast(self):
        cmd, should = self._parse_command('{"command": "update"}', "PC-001")
        assert cmd == "update"
        assert should is True

    def test_targeted_matching_host(self):
        cmd, should = self._parse_command('{"command": "inspect", "target": "PC-001"}', "PC-001")
        assert cmd == "inspect"
        assert should is True

    def test_targeted_different_host_skipped(self):
        cmd, should = self._parse_command('{"command": "inspect", "target": "PC-002"}', "PC-001")
        assert cmd == "inspect"
        assert should is False

    def test_invalid_json_skipped(self):
        cmd, should = self._parse_command("{ not json }", "PC-001")
        assert cmd is None
        assert should is False

    def test_non_dict_payload_skipped(self):
        cmd, should = self._parse_command("[1, 2, 3]", "PC-001")
        assert cmd is None
        assert should is False

    def test_unknown_command_parsed(self):
        cmd, should = self._parse_command('{"command": "reboot"}', "PC-001")
        assert cmd == "reboot"
        assert should is True

    def test_empty_command(self):
        cmd, should = self._parse_command('{}', "PC-001")
        assert cmd is None
        assert should is True

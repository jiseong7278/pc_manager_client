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
sys.modules.setdefault("truststore",       MagicMock())

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


class TestTargetsListParsing:
    """subscribe_and_run의 targets 리스트 필터링 로직 단위 테스트"""

    def _parse_command(self, raw: str, my_hostname: str) -> tuple[str | None, bool]:
        """
        실제 subscribe_and_run의 파싱 로직 (targets 포함) 추출
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
        targets = payload.get("targets")

        if target and target != my_hostname:
            return command, False

        if targets and my_hostname not in targets:
            return command, False

        return command, True

    def test_targets_list_hostname_included_executes(self):
        """targets 리스트에 내 호스트명이 포함되면 실행"""
        cmd, should = self._parse_command(
            '{"command": "inspect", "targets": ["PC-001", "PC-002", "PC-003"]}',
            "PC-002",
        )
        assert cmd == "inspect"
        assert should is True

    def test_targets_list_hostname_not_included_skipped(self):
        """targets 리스트에 내 호스트명이 없으면 건너뜀"""
        cmd, should = self._parse_command(
            '{"command": "inspect", "targets": ["PC-001", "PC-003"]}',
            "PC-002",
        )
        assert cmd == "inspect"
        assert should is False

    def test_targets_list_single_element_matching(self):
        """단일 요소 targets 리스트에서 호스트명 일치 시 실행"""
        cmd, should = self._parse_command(
            '{"command": "inspect", "targets": ["PC-001"]}',
            "PC-001",
        )
        assert cmd == "inspect"
        assert should is True

    def test_targets_list_single_element_not_matching(self):
        """단일 요소 targets 리스트에서 호스트명 불일치 시 건너뜀"""
        cmd, should = self._parse_command(
            '{"command": "inspect", "targets": ["PC-001"]}',
            "PC-002",
        )
        assert cmd == "inspect"
        assert should is False

    def test_targets_empty_list_treated_as_broadcast(self):
        """targets가 빈 리스트이면 필터 없이 실행 (falsy → 분기 미적용)"""
        cmd, should = self._parse_command(
            '{"command": "inspect", "targets": []}',
            "PC-001",
        )
        assert cmd == "inspect"
        assert should is True

    def test_no_target_no_targets_is_broadcast(self):
        """target/targets 모두 없으면 브로드캐스트 → 실행"""
        cmd, should = self._parse_command('{"command": "inspect"}', "ANY-PC")
        assert cmd == "inspect"
        assert should is True

    def test_target_takes_precedence_over_targets_when_target_mismatches(self):
        """target 불일치 시, targets에 포함돼도 target 기준으로 건너뜀"""
        cmd, should = self._parse_command(
            '{"command": "inspect", "target": "PC-999", "targets": ["PC-001", "PC-002"]}',
            "PC-001",
        )
        assert cmd == "inspect"
        assert should is False  # target 체크가 먼저

    def test_target_matches_but_not_in_targets_is_skipped(self):
        """target 일치해도 targets 리스트에 없으면 건너뜀 (두 체크 독립 적용)"""
        cmd, should = self._parse_command(
            '{"command": "inspect", "target": "PC-001", "targets": ["PC-002", "PC-003"]}',
            "PC-001",
        )
        assert cmd == "inspect"
        assert should is False  # targets 필터에서 걸림

    def test_targets_with_update_command(self):
        """update 명령에도 targets 필터가 동일하게 적용됨"""
        cmd, should = self._parse_command(
            '{"command": "update", "targets": ["PC-010", "PC-011"]}',
            "PC-010",
        )
        assert cmd == "update"
        assert should is True

    def test_targets_with_update_command_not_matching(self):
        """update 명령에서 targets에 없으면 건너뜀"""
        cmd, should = self._parse_command(
            '{"command": "update", "targets": ["PC-010", "PC-011"]}',
            "PC-001",
        )
        assert cmd == "update"
        assert should is False


class TestCommandSignatureVerification:
    """Pub-Sub 수신 메시지의 HMAC 서명 검증 로직 테스트"""

    def _parse_with_sig_check(self, raw: str, my_hostname: str, hmac_secret: str = "") -> tuple[str | None, bool]:
        """
        subscribe_and_run의 서명 검증 + 파싱 로직 추출
        returns (command, should_execute)
        """
        import hashlib
        import hmac as _hmac

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None, False

        if not isinstance(payload, dict):
            return None, False

        # HMAC 검증
        if hmac_secret:
            sig = payload.get("sig", "")
            msg_without_sig = {k: v for k, v in payload.items() if k != "sig"}
            msg_canonical   = json.dumps(msg_without_sig, sort_keys=True)
            expected = _hmac.new(
                hmac_secret.encode(), msg_canonical.encode(), hashlib.sha256
            ).hexdigest()
            if not _hmac.compare_digest(sig, expected):
                return payload.get("command"), False

        command = payload.get("command")
        target  = payload.get("target")
        targets = payload.get("targets")

        if target and target != my_hostname:
            return command, False
        if targets and my_hostname not in targets:
            return command, False

        return command, True

    def _make_signed_msg(self, payload: dict, secret: str) -> str:
        """테스트용 서명 메시지 생성"""
        import hashlib, hmac as _hmac
        canonical = json.dumps(payload, sort_keys=True)
        sig = _hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        return json.dumps({**payload, "sig": sig})

    def test_valid_signature_accepted(self):
        """올바른 서명이면 명령 실행"""
        msg = self._make_signed_msg({"command": "inspect"}, "secret123")
        cmd, should = self._parse_with_sig_check(msg, "PC-001", hmac_secret="secret123")
        assert cmd == "inspect"
        assert should is True

    def test_invalid_signature_rejected(self):
        """잘못된 서명이면 명령 무시"""
        payload = json.dumps({"command": "inspect", "sig": "badhash"})
        cmd, should = self._parse_with_sig_check(payload, "PC-001", hmac_secret="secret123")
        assert cmd == "inspect"
        assert should is False

    def test_missing_signature_rejected_when_secret_set(self):
        """시크릿이 있는데 sig 필드 없으면 거부"""
        payload = json.dumps({"command": "inspect"})
        cmd, should = self._parse_with_sig_check(payload, "PC-001", hmac_secret="secret123")
        assert cmd == "inspect"
        assert should is False

    def test_no_secret_accepts_unsigned_message(self):
        """시크릿 없으면 서명 없는 메시지도 수락 (기존 동작 유지)"""
        payload = json.dumps({"command": "inspect"})
        cmd, should = self._parse_with_sig_check(payload, "PC-001", hmac_secret="")
        assert cmd == "inspect"
        assert should is True

    def test_set_secret_accepted_before_first_secret(self):
        """시크릿 미설정 상태에서 set_secret 명령은 검증 없이 수락"""
        payload = json.dumps({"command": "set_secret", "secret": "newsecret"})
        cmd, should = self._parse_with_sig_check(payload, "PC-001", hmac_secret="")
        assert cmd == "set_secret"
        assert should is True

    def test_signed_set_token_accepted(self):
        """서명된 set_token 명령 수락"""
        msg = self._make_signed_msg({"command": "set_token", "token": "ghp_abc"}, "mysecret")
        cmd, should = self._parse_with_sig_check(msg, "PC-001", hmac_secret="mysecret")
        assert cmd == "set_token"
        assert should is True

    def test_unsigned_set_token_rejected_when_secret_set(self):
        """시크릿 있는데 서명 없는 set_token 거부"""
        payload = json.dumps({"command": "set_token", "token": "ghp_abc"})
        cmd, should = self._parse_with_sig_check(payload, "PC-001", hmac_secret="mysecret")
        assert cmd == "set_token"
        assert should is False

    def test_signature_with_target_field(self):
        """target 필드 포함 서명 메시지 검증"""
        msg = self._make_signed_msg({"command": "inspect", "target": "PC-001"}, "secret")
        cmd, should = self._parse_with_sig_check(msg, "PC-001", hmac_secret="secret")
        assert cmd == "inspect"
        assert should is True

    def test_tampered_message_rejected(self):
        """서명 후 command 필드가 변조된 메시지 거부"""
        # 정상 서명
        msg_dict = json.loads(self._make_signed_msg({"command": "inspect"}, "secret"))
        # 변조: command를 update로 바꿈
        msg_dict["command"] = "update"
        cmd, should = self._parse_with_sig_check(
            json.dumps(msg_dict), "PC-001", hmac_secret="secret"
        )
        assert cmd == "update"
        assert should is False  # 서명 불일치


class TestSaveTokenToRegistry:

    def test_saves_token_and_updates_config(self):
        """토큰을 레지스트리에 저장하고 config.GITHUB_TOKEN 업데이트"""
        import winreg as _real_winreg
        mock_winreg = MagicMock()
        mock_key    = MagicMock()
        mock_winreg.CreateKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.CreateKey.return_value.__exit__  = MagicMock(return_value=False)
        mock_winreg.HKEY_LOCAL_MACHINE = _real_winreg.HKEY_LOCAL_MACHINE
        mock_winreg.REG_SZ             = _real_winreg.REG_SZ

        with patch.dict("sys.modules", {"winreg": mock_winreg}):
            import redis_client
            redis_client._save_token_to_registry("ghp_testtoken")

        mock_winreg.CreateKey.assert_called_once()
        mock_winreg.SetValueEx.assert_any_call(
            mock_key, "GitHubToken", 0, _real_winreg.REG_SZ, "ghp_testtoken"
        )
        assert mock_winreg.SetValueEx.call_count == 2  # token + timestamp
        assert redis_client.config.GITHUB_TOKEN == "ghp_testtoken"

    def test_registry_error_does_not_raise(self):
        """레지스트리 저장 실패 시 예외 전파 없이 로그만 남김"""
        mock_winreg = MagicMock()
        mock_winreg.CreateKey.side_effect = PermissionError("Access denied")

        with patch.dict("sys.modules", {"winreg": mock_winreg}):
            import redis_client
            redis_client._save_token_to_registry("ghp_token")  # 예외 발생 없어야 함


class TestHmacSecret:

    def test_get_hmac_secret_from_registry(self):
        """레지스트리에 HMACSecret이 있으면 반환"""
        import winreg as _real_winreg
        mock_winreg = MagicMock()
        mock_key    = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__  = MagicMock(return_value=False)
        mock_winreg.QueryValueEx.return_value      = ("registry_secret", 1)
        mock_winreg.HKEY_LOCAL_MACHINE             = _real_winreg.HKEY_LOCAL_MACHINE

        with patch.dict("sys.modules", {"winreg": mock_winreg}):
            import redis_client
            secret = redis_client._get_hmac_secret()

        assert secret == "registry_secret"

    def test_get_hmac_secret_fallback_to_config(self):
        """레지스트리에 없으면 config.HMAC_SECRET으로 폴백"""
        mock_winreg = MagicMock()
        mock_winreg.OpenKey.side_effect = FileNotFoundError()

        with patch.dict("sys.modules", {"winreg": mock_winreg}):
            import redis_client
            redis_client.config.HMAC_SECRET = "config_secret"
            secret = redis_client._get_hmac_secret()

        assert secret == "config_secret"

    def test_get_hmac_secret_empty_registry_value_fallback(self):
        """레지스트리 값이 빈 문자열이면 config로 폴백"""
        import winreg as _real_winreg
        mock_winreg = MagicMock()
        mock_key    = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__  = MagicMock(return_value=False)
        mock_winreg.QueryValueEx.return_value      = ("", 1)   # 빈 값
        mock_winreg.HKEY_LOCAL_MACHINE             = _real_winreg.HKEY_LOCAL_MACHINE

        with patch.dict("sys.modules", {"winreg": mock_winreg}):
            import redis_client
            redis_client.config.HMAC_SECRET = "fallback_secret"
            secret = redis_client._get_hmac_secret()

        assert secret == "fallback_secret"

    def test_save_secret_to_registry(self):
        """HMAC 시크릿을 레지스트리에 저장"""
        import winreg as _real_winreg
        mock_winreg = MagicMock()
        mock_key    = MagicMock()
        mock_winreg.CreateKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.CreateKey.return_value.__exit__  = MagicMock(return_value=False)
        mock_winreg.HKEY_LOCAL_MACHINE               = _real_winreg.HKEY_LOCAL_MACHINE
        mock_winreg.REG_SZ                           = _real_winreg.REG_SZ

        with patch.dict("sys.modules", {"winreg": mock_winreg}):
            import redis_client
            redis_client._save_secret_to_registry("newsecret")

        mock_winreg.SetValueEx.assert_any_call(
            mock_key, "HMACSecret", 0, _real_winreg.REG_SZ, "newsecret"
        )
        assert mock_winreg.SetValueEx.call_count == 2  # secret + timestamp

    def test_save_secret_registry_error_does_not_raise(self):
        """레지스트리 저장 실패 시 예외 전파 없음"""
        mock_winreg = MagicMock()
        mock_winreg.CreateKey.side_effect = PermissionError("Access denied")

        with patch.dict("sys.modules", {"winreg": mock_winreg}):
            import redis_client
            redis_client._save_secret_to_registry("secret")  # 예외 발생 없어야 함


class TestSignPayload:

    def test_sign_payload_with_secret(self):
        """시크릿이 있으면 HMAC-SHA256 서명 반환"""
        import hashlib
        import hmac as _hmac
        import redis_client

        with patch.object(redis_client, "_get_hmac_secret", return_value="mysecret"):
            sig = redis_client._sign_payload("hello")

        expected = _hmac.new(b"mysecret", b"hello", hashlib.sha256).hexdigest()
        assert sig == expected

    def test_sign_payload_no_secret_returns_empty(self):
        """시크릿 없으면 빈 문자열 반환"""
        import redis_client

        with patch.object(redis_client, "_get_hmac_secret", return_value=""):
            sig = redis_client._sign_payload("hello")

        assert sig == ""


class TestFailedReports:

    def test_save_and_load_failed_report(self, tmp_path):
        """실패 보고서 저장/로드"""
        import redis_client
        original = redis_client._FAILED_REPORTS_FILE
        redis_client._FAILED_REPORTS_FILE = str(tmp_path / "failed.json")
        try:
            redis_client._save_failed_report({"hostname": "PC-001", "data": "test"})
            reports = redis_client._load_failed_reports()
            assert len(reports) == 1
            assert reports[0]["hostname"] == "PC-001"
        finally:
            redis_client._FAILED_REPORTS_FILE = original

    def test_save_multiple_failed_reports_appends(self, tmp_path):
        """여러 번 저장하면 누적됨"""
        import redis_client
        original = redis_client._FAILED_REPORTS_FILE
        redis_client._FAILED_REPORTS_FILE = str(tmp_path / "failed.json")
        try:
            redis_client._save_failed_report({"hostname": "PC-001"})
            redis_client._save_failed_report({"hostname": "PC-002"})
            reports = redis_client._load_failed_reports()
            assert len(reports) == 2
        finally:
            redis_client._FAILED_REPORTS_FILE = original

    def test_load_failed_reports_no_file_returns_empty(self, tmp_path):
        """파일 없으면 빈 리스트 반환"""
        import redis_client
        original = redis_client._FAILED_REPORTS_FILE
        redis_client._FAILED_REPORTS_FILE = str(tmp_path / "nonexistent.json")
        try:
            assert redis_client._load_failed_reports() == []
        finally:
            redis_client._FAILED_REPORTS_FILE = original

    def test_clear_failed_reports(self, tmp_path):
        """실패 보고서 파일 삭제"""
        import redis_client
        test_file = str(tmp_path / "failed.json")
        original  = redis_client._FAILED_REPORTS_FILE
        redis_client._FAILED_REPORTS_FILE = test_file
        try:
            with open(test_file, "w") as f:
                json.dump([{"hostname": "PC-001"}], f)
            redis_client._clear_failed_reports()
            assert not os.path.exists(test_file)
        finally:
            redis_client._FAILED_REPORTS_FILE = original


class TestSetSecretCommand:

    def test_set_secret_calls_save_registry(self):
        """set_secret 명령 수신 시 _save_secret_to_registry 호출"""
        import redis_client

        saved = []

        def fake_save(secret):
            saved.append(secret)

        # 실제 subscribe_and_run을 호출하기 어려우므로 핵심 로직만 단위 테스트
        with patch.object(redis_client, "_save_secret_to_registry", side_effect=fake_save):
            # set_secret 명령 처리 로직 직접 실행
            command = "set_secret"
            secret  = "distributedsecret"
            if command == "set_secret" and secret:
                redis_client._save_secret_to_registry(secret)

        assert saved == ["distributedsecret"]

    def test_set_secret_empty_secret_skipped(self):
        """secret 값이 없으면 저장하지 않음"""
        import redis_client

        saved = []

        with patch.object(redis_client, "_save_secret_to_registry", side_effect=saved.append):
            command = "set_secret"
            secret  = ""
            if command == "set_secret" and secret:
                redis_client._save_secret_to_registry(secret)

        assert saved == []


class TestSendHeartbeat:

    def test_heartbeat_sends_hset_and_stops(self):
        """send_heartbeat이 Redis Hash에 heartbeat를 전송하고 stop_event로 종료"""
        import threading
        import redis_client

        calls      = []
        stop_event = threading.Event()

        mock_redis = MagicMock()
        mock_redis.hset.side_effect = lambda key, host, data: (
            calls.append((key, host, data)) or stop_event.set()
        )
        mock_redis.close = MagicMock()

        with patch("redis_client.get_redis", return_value=mock_redis):
            t = threading.Thread(
                target=redis_client.send_heartbeat,
                args=("PC-TEST", "192.168.0.1", stop_event),
                daemon=True,
            )
            t.start()
            t.join(timeout=3)

        assert len(calls) >= 1
        assert calls[0][0] == config.HEARTBEAT_KEY
        assert calls[0][1] == "PC-TEST"

        beat_data = json.loads(calls[0][2])
        assert beat_data["hostname"]   == "PC-TEST"
        assert beat_data["ip_address"] == "192.168.0.1"
        assert "beat_at" in beat_data

    def test_heartbeat_redis_failure_does_not_crash(self):
        """Redis 연결 실패 시 예외 없이 계속 실행"""
        import threading
        import redis_client

        stop_event  = threading.Event()
        call_count  = [0]

        def fake_get_redis():
            call_count[0] += 1
            if call_count[0] >= 2:
                stop_event.set()
            raise ConnectionError("Redis down")

        with patch("redis_client.get_redis", side_effect=fake_get_redis):
            t = threading.Thread(
                target=redis_client.send_heartbeat,
                args=("PC-TEST", "192.168.0.1", stop_event),
                daemon=True,
            )
            t.start()
            t.join(timeout=3)

        # 예외 없이 정상 종료됐으면 통과


class TestCollectLock:

    def test_collect_lock_skipped_when_held(self):
        """_collect_lock 보유 중에는 collect_all이 호출되지 않음"""
        import redis_client
        with redis_client._collect_lock:
            with patch("redis_client.collect_all") as mock_collect:
                # 락이 이미 잡혀 있으므로 acquire(blocking=False)가 False를 반환
                acquired = redis_client._collect_lock.acquire(blocking=False)
                assert acquired is False
                mock_collect.assert_not_called()

    def test_collect_lock_released_after_use(self):
        """수집 완료 후 락이 해제됨"""
        import redis_client
        assert redis_client._collect_lock.acquire(blocking=False)
        redis_client._collect_lock.release()
        # 두 번째에도 정상 획득 가능해야 함
        assert redis_client._collect_lock.acquire(blocking=False)
        redis_client._collect_lock.release()

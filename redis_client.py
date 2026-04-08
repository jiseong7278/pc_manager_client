# redis_client.py
# Redis Pub-Sub 수신 + Stream 전송

import hashlib
import hmac as _hmac_module
import json
import logging
import os
import socket
import threading
import time
from contextlib import suppress
from datetime import datetime, timezone, timedelta

import redis
import websocket as _ws

import config
from collector import collect_all
from updater import trigger_update

logger = logging.getLogger(__name__)

_collect_lock = threading.Lock()

# subscribe_and_run 이 heartbeat_request 명령을 수신하면 set.
# send_heartbeat_ws 가 감지해 즉시 heartbeat를 전송한다.
_heartbeat_now_event = threading.Event()

_REGISTRY_KEY       = r"SOFTWARE\PCInspector"

# Redis 재연결 백오프 시퀀스 (초): 5 → 10 → 30 → 60 → 120 후 유지
_RETRY_DELAYS = [5, 10, 30, 60, 120]
_FAILED_REPORTS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "failed_reports.json"
)

KST = timezone(timedelta(hours=9))


# DPAPI 플래그: CRYPTPROTECT_LOCAL_MACHINE — 동일 PC의 어느 계정에서나 복호화 가능
_DPAPI_FLAG_LOCAL_MACHINE = 4


def _dpapi_encrypt(value: str) -> bytes:
    """Windows DPAPI로 문자열을 암호화해 bytes로 반환"""
    import win32crypt
    return win32crypt.CryptProtectData(
        value.encode("utf-8"), None, None, None, None, _DPAPI_FLAG_LOCAL_MACHINE
    )


def _dpapi_decrypt(data) -> str:
    """Windows DPAPI로 bytes를 복호화해 문자열로 반환.
    레거시 평문 str 값(이전 버전 설치분)은 그대로 반환한다."""
    if isinstance(data, str):
        return data  # 레거시 평문값 — 다음 저장 시 자동으로 암호화됨
    try:
        import win32crypt
        _, decrypted = win32crypt.CryptUnprotectData(data, None, None, None, 0)
        return decrypted.decode("utf-8")
    except Exception as e:
        logger.warning(f"DPAPI 복호화 실패: {e}")
        return ""


def _save_token_to_registry(token: str) -> None:
    """GitHub Token과 업데이트 시각을 레지스트리 HKLM\\SOFTWARE\\PCInspector에 저장 (DPAPI 암호화)"""
    try:
        import winreg
        now = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S")
        with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, _REGISTRY_KEY) as key:
            winreg.SetValueEx(key, "GitHubToken",          0, winreg.REG_BINARY, _dpapi_encrypt(token))
            winreg.SetValueEx(key, "GitHubTokenUpdatedAt", 0, winreg.REG_SZ,     now)
        config.GITHUB_TOKEN = token
        logger.info("GitHub Token 레지스트리 저장 완료 (DPAPI 암호화)")
    except Exception as e:
        logger.error(f"GitHub Token 레지스트리 저장 실패: {e}")


def _get_token_info() -> dict:
    """레지스트리에서 GitHub Token 존재 여부와 최종 업데이트 시각 조회"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _REGISTRY_KEY) as key:
            token, _ = winreg.QueryValueEx(key, "GitHubToken")
            token_exists = bool(token)
            try:
                updated_at, _ = winreg.QueryValueEx(key, "GitHubTokenUpdatedAt")
            except FileNotFoundError:
                updated_at = ""
        return {"token_exists": token_exists, "token_updated_at": updated_at}
    except FileNotFoundError:
        return {"token_exists": False, "token_updated_at": ""}
    except Exception as e:
        logger.warning(f"Token 정보 조회 실패: {e}")
        return {"token_exists": False, "token_updated_at": ""}


# ── HMAC 서명 ─────────────────────────────────────────────────────────

def _get_hmac_secret() -> str:
    """
    HMAC 시크릿을 레지스트리(HKLM\\SOFTWARE\\PCInspector\\HMACSecret)에서 읽는다.
    레지스트리에 없으면 config.py 기본값을 사용한다.
    """
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _REGISTRY_KEY) as key:
            raw, _ = winreg.QueryValueEx(key, "HMACSecret")
            secret  = _dpapi_decrypt(raw)
            if secret:
                return secret
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"HMACSecret 레지스트리 조회 실패: {e}")
    return config.HMAC_SECRET


def _save_secret_to_registry(secret: str) -> None:
    """HMAC 시크릿을 레지스트리에 저장 (DPAPI 암호화)"""
    try:
        import winreg
        now = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S")
        with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, _REGISTRY_KEY) as key:
            winreg.SetValueEx(key, "HMACSecret",          0, winreg.REG_BINARY, _dpapi_encrypt(secret))
            winreg.SetValueEx(key, "HMACSecretUpdatedAt", 0, winreg.REG_SZ,     now)
        logger.info("HMAC 시크릿 레지스트리 저장 완료 (DPAPI 암호화)")
    except Exception as e:
        logger.error(f"HMAC 시크릿 레지스트리 저장 실패: {e}")


def _sign_payload(payload_str: str) -> str:
    """HMAC-SHA256 서명 생성. 시크릿이 없으면 빈 문자열 반환"""
    secret = _get_hmac_secret()
    if not secret:
        return ""
    return _hmac_module.new(
        secret.encode(),
        payload_str.encode(),
        hashlib.sha256,
    ).hexdigest()


# ── 실패 보고서 로컬 캐싱 ──────────────────────────────────────────────

def _save_failed_report(data: dict) -> None:
    """전송 실패한 보고서를 로컬 파일에 저장"""
    try:
        existing = _load_failed_reports()
        existing.append(data)
        with open(_FAILED_REPORTS_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False)
        logger.info(f"실패 보고서 로컬 저장: 누적 {len(existing)}건")
    except Exception as e:
        logger.error(f"실패 보고서 저장 오류: {e}")


def _load_failed_reports() -> list:
    try:
        if os.path.exists(_FAILED_REPORTS_FILE):
            with open(_FAILED_REPORTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _clear_failed_reports() -> None:
    try:
        if os.path.exists(_FAILED_REPORTS_FILE):
            os.remove(_FAILED_REPORTS_FILE)
    except Exception:
        pass


def _retry_failed_reports(r: redis.Redis) -> None:
    """로컬에 캐싱된 실패 보고서 재전송 시도"""
    failed = _load_failed_reports()
    if not failed:
        return
    logger.info(f"캐싱된 실패 보고서 재전송 시도: {len(failed)}건")
    sent = 0
    remaining = list(failed)
    for data in failed:
        try:
            payload_str = json.dumps(data, ensure_ascii=False)
            sig = _sign_payload(payload_str)
            fields: dict = {"data": payload_str}
            if sig:
                fields["sig"] = sig
            r.xadd(config.STREAM_KEY, fields, maxlen=5000, approximate=True)
            remaining.pop(0)
            sent += 1
        except Exception as e:
            logger.error(f"실패 보고서 재전송 중 오류 (전송 {sent}건 후): {e}")
            break
    if sent == len(failed):
        _clear_failed_reports()
        logger.info(f"실패 보고서 {sent}건 재전송 완료")
    elif sent > 0:
        # 일부만 성공 — 미전송 항목만 파일에 다시 저장
        try:
            with open(_FAILED_REPORTS_FILE, "w", encoding="utf-8") as f:
                json.dump(remaining, f, ensure_ascii=False)
            logger.info(f"실패 보고서 {sent}건 전송, {len(remaining)}건 재저장")
        except Exception as e:
            logger.error(f"실패 보고서 재저장 오류: {e}")


# ── Redis 연결 ─────────────────────────────────────────────────────────

def get_redis() -> redis.Redis:
    kwargs: dict = dict(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    if config.REDIS_PASSWORD:
        kwargs["password"] = config.REDIS_PASSWORD
    if config.REDIS_TLS_ENABLED:
        kwargs["ssl"] = True
    return redis.Redis(**kwargs)


def publish_result(data: dict) -> None:
    """수집 데이터를 Redis Stream으로 전송. 실패 시 로컬 파일에 캐싱"""
    payload_str = json.dumps(data, ensure_ascii=False)
    sig         = _sign_payload(payload_str)
    fields: dict = {"data": payload_str}
    if sig:
        fields["sig"] = sig

    r = get_redis()
    try:
        r.xadd(config.STREAM_KEY, fields, maxlen=5000, approximate=True)
        logger.info(f"Stream 전송 완료: {config.STREAM_KEY}")
        # 전송 성공 시 이전에 캐싱된 실패 보고서도 재시도
        _retry_failed_reports(r)
    except Exception as e:
        logger.error(f"Stream 전송 실패: {e} → 로컬 캐싱")
        _save_failed_report(data)
    finally:
        r.close()


def send_heartbeat_ws(hostname: str, ip_address: str, stop_event: threading.Event) -> None:
    """
    주기적으로 heartbeat를 서버 WebSocket으로 전송하는 루프.
    서버가 접속/해제 시점을 즉시 감지해 온라인 PC 목록을 관리한다.

    stop_event.wait() 대신 1초 단위로 ws.recv()를 호출해
    서버의 WebSocket PING 프레임에 PONG을 자동 응답한다.
    (uvicorn/websockets는 기본 20초마다 PING 전송 — 무응답 시 연결 종료)
    """
    import ssl
    import truststore

    sep = "&" if "?" in config.SERVER_WS_URL else "?"
    url = f"{config.SERVER_WS_URL}{sep}device_type=pc"
    headers = {}
    if config.SERVER_API_KEY:
        headers["Authorization"] = f"Bearer {config.SERVER_API_KEY}"

    ssl_ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ssl_ctx.check_hostname = True
    ssl_ctx.verify_mode    = ssl.CERT_REQUIRED

    retry_count = 0

    while not stop_event.is_set():
        ws = None
        try:
            ws = _ws.WebSocket()
            ws.connect(url, timeout=10, header=headers, sslopt={"context": ssl_ctx})
            ws.settimeout(1)  # recv() 1초 대기 후 타임아웃 → PING 응답 루프용
            logger.info(f"Heartbeat WS 연결: {config.SERVER_WS_URL}")
            retry_count = 0

            next_beat = time.monotonic()
            while not stop_event.is_set():
                now = time.monotonic()
                if now >= next_beat or _heartbeat_now_event.is_set():
                    _heartbeat_now_event.clear()
                    beat = json.dumps({
                        "type":       "heartbeat",
                        "hostname":   hostname,
                        "ip_address": ip_address,
                        "version":    config.CLIENT_VERSION,
                    }, ensure_ascii=False)
                    ws.send(beat)
                    logger.debug(f"Heartbeat WS 전송: {hostname}")
                    next_beat = now + config.HEARTBEAT_INTERVAL
                try:
                    ws.recv()  # 수신 프레임 소비; PING → 자동 PONG 응답
                except _ws.WebSocketTimeoutException:
                    pass  # 1초 내 프레임 없음 — 정상

        except Exception as e:
            delay = _RETRY_DELAYS[min(retry_count, len(_RETRY_DELAYS) - 1)]
            logger.warning(f"Heartbeat WS 오류: {e} - {delay}초 후 재시도 (#{retry_count + 1})")
            retry_count += 1
            stop_event.wait(delay)
        finally:
            if ws:
                with suppress(Exception):
                    ws.close()


def subscribe_and_run(stop_event) -> None:
    """
    Redis Pub-Sub 채널 구독 루프
    서버에서 명령 수신 시 처리:
      inspect - PC 데이터 수집 후 Stream 전송
      update  - 클라이언트 업데이트 실행
    """
    hostname    = socket.gethostname()
    retry_count = 0

    while not stop_event.is_set():
        r      = None
        pubsub = None
        try:
            r = get_redis()
            pubsub = r.pubsub()
            pubsub.subscribe(config.REDIS_CHANNEL)
            logger.info(f"Redis 채널 구독 시작: {config.REDIS_CHANNEL}")
            retry_count = 0  # 연결 성공 시 카운터 초기화

            for message in pubsub.listen():
                if stop_event.is_set():
                    break

                if message["type"] != "message":
                    continue

                try:
                    payload = json.loads(message["data"])
                except json.JSONDecodeError:
                    logger.warning(f"잘못된 메시지 형식: {message['data']}")
                    continue

                if not isinstance(payload, dict):
                    logger.warning(f"메시지가 dict가 아님: {type(payload)}")
                    continue

                # HMAC 서명 검증 (시크릿이 등록된 경우)
                # set_secret / set_token / set_api_key 는 설정 부트스트랩 명령이므로 검증 제외
                # sig 필드를 제외한 나머지를 sort_keys 직렬화로 검증
                _cmd_raw    = payload.get("command")
                hmac_secret = _get_hmac_secret()
                if hmac_secret and _cmd_raw not in ("set_secret", "set_token", "set_api_key"):
                    sig = payload.get("sig", "")
                    msg_without_sig = {k: v for k, v in payload.items() if k != "sig"}
                    msg_canonical   = json.dumps(msg_without_sig, sort_keys=True, separators=(',', ':'))
                    expected = _hmac_module.new(
                        hmac_secret.encode(), msg_canonical.encode(), hashlib.sha256
                    ).hexdigest()
                    if not _hmac_module.compare_digest(sig, expected):
                        logger.warning(
                            f"명령 서명 검증 실패, 무시 | command={_cmd_raw}"
                        )
                        continue

                command = payload.get("command")
                target  = payload.get("target")
                targets = payload.get("targets")  # 다중 타겟 리스트

                # 단일 target이 지정된 경우 내 호스트명과 일치할 때만 실행
                if target and target != hostname:
                    continue

                # targets 리스트가 지정된 경우 내 호스트명이 포함될 때만 실행
                if targets and hostname not in targets:
                    continue

                if command == "inspect":
                    logger.info(f"점검 명령 수신 (target={target or 'all'})")
                    if not _collect_lock.acquire(blocking=False):
                        logger.warning("수집이 이미 진행 중입니다. 명령 무시")
                        continue
                    try:
                        session_id = payload.get("session_id", "")
                        data = collect_all()
                        data["hostname"]   = hostname
                        data["ip_address"] = _get_ip_address()
                        data.update(_get_token_info())
                        if session_id:
                            data["session_id"] = session_id
                        publish_result(data)
                    except Exception as e:
                        logger.error(f"데이터 수집/전송 실패: {e}")
                    finally:
                        _collect_lock.release()

                elif command == "update":
                    logger.info(f"업데이트 명령 수신 (target={target or 'all'})")
                    try:
                        trigger_update()
                    except Exception as e:
                        logger.error(f"업데이트 실행 실패: {e}")

                elif command == "av_update":
                    logger.info(f"백신 업데이트 명령 수신 (target={target or 'all'})")
                    try:
                        from collector import trigger_av_update
                        result = trigger_av_update()
                        logger.info(
                            "백신 업데이트 완료 | updated=%s skipped=%s errors=%s",
                            result.get("updated"), result.get("skipped"), result.get("errors"),
                        )
                    except Exception as e:
                        logger.error(f"백신 업데이트 실행 실패: {e}")

                elif command == "set_token":
                    token_type = payload.get("token_type", "")
                    if token_type != "pc":
                        logger.debug(f"set_token 무시: token_type={token_type!r} (PC 전용 아님)")
                        continue
                    token = payload.get("token", "")
                    if token:
                        _save_token_to_registry(token)
                    else:
                        logger.warning("set_token 명령에 token 값 없음, 무시")

                elif command == "set_secret":
                    secret = payload.get("secret", "")
                    if secret:
                        _save_secret_to_registry(secret)
                    else:
                        logger.warning("set_secret 명령에 secret 값 없음, 무시")

                elif command == "heartbeat_request":
                    logger.info("즉시 heartbeat 요청 수신")
                    _heartbeat_now_event.set()

                else:
                    logger.warning(f"알 수 없는 명령: {command!r}")

        except redis.ConnectionError as e:
            delay = _RETRY_DELAYS[min(retry_count, len(_RETRY_DELAYS) - 1)]
            logger.error(f"Redis 연결 실패: {e} - {delay}초 후 재시도 (#{retry_count + 1})")
            retry_count += 1
            stop_event.wait(delay)
        except Exception as e:
            delay = _RETRY_DELAYS[min(retry_count, len(_RETRY_DELAYS) - 1)]
            logger.error(f"예상치 못한 오류: {e} - {delay}초 후 재시도 (#{retry_count + 1})")
            retry_count += 1
            stop_event.wait(delay)
        finally:
            try:
                if pubsub:
                    pubsub.unsubscribe()
                if r:
                    r.close()
            except Exception:
                pass


def _get_ip_address() -> str:
    """현재 PC의 IP 주소 조회"""
    try:
        # 외부 연결용 소켓으로 실제 사용 중인 IP 확인
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((config.REDIS_HOST, config.REDIS_PORT))
            return s.getsockname()[0]
    except Exception:
        return socket.gethostbyname(socket.gethostname())
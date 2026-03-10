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
from datetime import datetime, timezone, timedelta

import redis

import config
from collector import collect_all
from updater import trigger_update

logger = logging.getLogger(__name__)

_collect_lock = threading.Lock()

_REGISTRY_KEY       = r"SOFTWARE\PCInspector"
_FAILED_REPORTS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "failed_reports.json"
)

KST = timezone(timedelta(hours=9))


def _save_token_to_registry(token: str) -> None:
    """GitHub Token과 업데이트 시각을 레지스트리 HKLM\\SOFTWARE\\PCInspector에 저장"""
    try:
        import winreg
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, _REGISTRY_KEY) as key:
            winreg.SetValueEx(key, "GitHubToken",          0, winreg.REG_SZ, token)
            winreg.SetValueEx(key, "GitHubTokenUpdatedAt", 0, winreg.REG_SZ, now)
        config.GITHUB_TOKEN = token
        logger.info("GitHub Token 레지스트리 저장 완료")
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
            secret, _ = winreg.QueryValueEx(key, "HMACSecret")
            if secret:
                return secret
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"HMACSecret 레지스트리 조회 실패: {e}")
    return config.HMAC_SECRET


def _save_secret_to_registry(secret: str) -> None:
    """HMAC 시크릿을 레지스트리에 저장"""
    try:
        import winreg
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, _REGISTRY_KEY) as key:
            winreg.SetValueEx(key, "HMACSecret",          0, winreg.REG_SZ, secret)
            winreg.SetValueEx(key, "HMACSecretUpdatedAt", 0, winreg.REG_SZ, now)
        logger.info("HMAC 시크릿 레지스트리 저장 완료")
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
    try:
        for data in failed:
            payload_str = json.dumps(data, ensure_ascii=False)
            sig = _sign_payload(payload_str)
            fields: dict = {"data": payload_str}
            if sig:
                fields["sig"] = sig
            r.xadd(config.STREAM_KEY, fields, maxlen=5000, approximate=True)
        _clear_failed_reports()
        logger.info(f"실패 보고서 {len(failed)}건 재전송 완료")
    except Exception as e:
        logger.error(f"실패 보고서 재전송 중 오류: {e}")


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


def send_heartbeat(hostname: str, ip_address: str, stop_event: threading.Event) -> None:
    """
    주기적으로 heartbeat를 Redis Hash에 저장하는 루프.
    서버가 이 데이터를 읽어 온라인 PC 목록을 파악한다.
    """
    while not stop_event.is_set():
        try:
            r = get_redis()
            try:
                beat_data = json.dumps({
                    "hostname":   hostname,
                    "ip_address": ip_address,
                    "version":    config.CLIENT_VERSION,
                    "beat_at":    datetime.now(timezone.utc).isoformat(),
                }, ensure_ascii=False)
                r.hset(config.HEARTBEAT_KEY, hostname, beat_data)
                logger.debug(f"Heartbeat 전송: {hostname}")
            finally:
                r.close()
        except Exception as e:
            logger.warning(f"Heartbeat 전송 실패: {e}")
        stop_event.wait(config.HEARTBEAT_INTERVAL)


def subscribe_and_run(stop_event) -> None:
    """
    Redis Pub-Sub 채널 구독 루프
    서버에서 명령 수신 시 처리:
      inspect - PC 데이터 수집 후 Stream 전송
      update  - 클라이언트 업데이트 실행
    """
    hostname = socket.gethostname()

    while not stop_event.is_set():
        r = None
        pubsub = None
        try:
            r = get_redis()
            pubsub = r.pubsub()
            pubsub.subscribe(config.REDIS_CHANNEL)
            logger.info(f"Redis 채널 구독 시작: {config.REDIS_CHANNEL}")

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
                        data = collect_all()
                        data["hostname"]   = hostname
                        data["ip_address"] = _get_ip_address()
                        data.update(_get_token_info())
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

                elif command == "set_token":
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

                else:
                    logger.warning(f"알 수 없는 명령: {command!r}")

        except redis.ConnectionError as e:
            logger.error(f"Redis 연결 실패: {e} - 5초 후 재시도")
            time.sleep(5)
        except Exception as e:
            logger.error(f"예상치 못한 오류: {e} - 5초 후 재시도")
            time.sleep(5)
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
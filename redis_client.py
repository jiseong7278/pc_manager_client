# redis_client.py
# Redis Pub-Sub 수신 + Stream 전송

import json
import logging
import socket
import threading
import time
from datetime import datetime

import redis

import config
from collector import collect_all
from updater import trigger_update

logger = logging.getLogger(__name__)

_collect_lock = threading.Lock()

_REGISTRY_KEY = r"SOFTWARE\PCInspector"


def _save_token_to_registry(token: str) -> None:
    """GitHub Token과 업데이트 시각을 레지스트리 HKLM\SOFTWARE\PCInspector에 저장"""
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
    """수집 데이터를 Redis Stream으로 전송"""
    r = get_redis()
    try:
        r.xadd(
            config.STREAM_KEY,
            {"data": json.dumps(data, ensure_ascii=False)},
            maxlen=5000,
            approximate=True,
        )
        logger.info(f"Stream 전송 완료: {config.STREAM_KEY}")
    finally:
        r.close()


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

                # target이 지정된 경우 내 호스트명과 일치할 때만 실행
                if target and target != hostname:
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
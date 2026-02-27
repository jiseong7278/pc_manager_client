# redis_client.py
# Redis Pub-Sub 수신 + Stream 전송

import json
import logging
import socket
import time

import redis

import config
from collector import collect_all

logger = logging.getLogger(__name__)


def get_redis() -> redis.Redis:
    return redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )


def publish_result(data: dict) -> None:
    """수집 데이터를 Redis Stream으로 전송"""
    r = get_redis()
    try:
        r.xadd(
            config.STREAM_KEY,
            {"data": json.dumps(data, ensure_ascii=False)},
            maxlen=5000,        # 스트림 최대 길이 유지
            approximate=True,
        )
        logger.info(f"Stream 전송 완료: {config.STREAM_KEY}")
    finally:
        r.close()


def subscribe_and_run(stop_event) -> None:
    """
    Redis Pub-Sub 채널 구독 루프
    서버에서 inspect 명령 수신 시 데이터 수집 후 Stream 전송
    stop_event: threading.Event - 서비스 종료 시 루프 탈출용
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

                command = payload.get("command")
                target  = payload.get("target")   # 특정 PC 지정 시

                # target 이 지정된 경우 내 호스트명과 일치할 때만 실행
                if target and target != hostname:
                    continue

                if command == "inspect":
                    logger.info(f"점검 명령 수신 (command={command}, target={target or 'all'})")
                    try:
                        data = collect_all()
                        data["hostname"] = hostname
                        publish_result(data)
                    except Exception as e:
                        logger.error(f"데이터 수집/전송 실패: {e}")

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

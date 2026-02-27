# service.py
# Windows 서비스 정의 및 실행

import logging
import threading

import servicemanager
import win32event
import win32service
import win32serviceutil

import config
from redis_client import subscribe_and_run
from updater import check_and_update

logger = logging.getLogger(__name__)


def _setup_logging():
    import os
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)

    from logging.handlers import TimedRotatingFileHandler
    handler = TimedRotatingFileHandler(
        filename=os.path.join(log_dir, "client.log"),
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    ))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


class PCInspectService(win32serviceutil.ServiceFramework):
    _svc_name_         = config.SERVICE_NAME
    _svc_display_name_ = config.SERVICE_DISPLAY
    _svc_description_  = config.SERVICE_DESC

    def __init__(self, args):
        super().__init__(args)
        self._stop_event = threading.Event()
        self._hWaitStop  = win32event.CreateEvent(None, 0, 0, None)

    def SvcStop(self):
        """서비스 중지 요청 처리"""
        logger.info("서비스 중지 요청")
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self._stop_event.set()
        win32event.SetEvent(self._hWaitStop)

    def SvcDoRun(self):
        """서비스 실행 진입점"""
        _setup_logging()
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, "")
        )
        logger.info(f"서비스 시작 (버전: {config.CLIENT_VERSION})")
        self._run()

    def _run(self):
        # Redis 구독 스레드
        redis_thread = threading.Thread(
            target=subscribe_and_run,
            args=(self._stop_event,),
            name="RedisSubscriber",
            daemon=True,
        )
        redis_thread.start()
        logger.info("Redis 구독 스레드 시작")

        # 자동 업데이트 스레드
        update_thread = threading.Thread(
            target=check_and_update,
            args=(self._stop_event,),
            name="AutoUpdater",
            daemon=True,
        )
        update_thread.start()
        logger.info("자동 업데이트 스레드 시작")

        # 서비스 중지 신호 대기
        win32event.WaitForSingleObject(self._hWaitStop, win32event.INFINITE)

        # 스레드 종료 대기 (최대 10초)
        redis_thread.join(timeout=10)
        update_thread.join(timeout=10)
        logger.info("서비스 종료 완료")

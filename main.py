# main.py
# 진입점: 서비스 설치, 제거, 디버그 실행

import sys


def print_usage():
    print("""
PC Inspect Client

사용법:
  PCInspectClient.exe install   - Windows 서비스 설치
  PCInspectClient.exe start     - 서비스 시작
  PCInspectClient.exe stop      - 서비스 중지
  PCInspectClient.exe remove    - 서비스 제거
  PCInspectClient.exe debug     - 서비스 없이 직접 실행 (테스트용)
""")


def run_debug():
    """서비스 없이 직접 실행 (개발/테스트용)"""
    import logging
    import threading

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

    import config
    from redis_client import subscribe_and_run
    from updater import check_and_update

    print(f"[DEBUG] PC Inspect Client v{config.CLIENT_VERSION} 디버그 모드 시작")
    print(f"[DEBUG] Redis: {config.REDIS_HOST}:{config.REDIS_PORT}")
    print(f"[DEBUG] 채널: {config.REDIS_CHANNEL}")
    print("[DEBUG] 종료: Ctrl+C")

    stop_event = threading.Event()

    redis_thread = threading.Thread(
        target=subscribe_and_run,
        args=(stop_event,),
        daemon=True,
    )
    update_thread = threading.Thread(
        target=check_and_update,
        args=(stop_event,),
        daemon=True,
    )

    redis_thread.start()
    update_thread.start()

    try:
        redis_thread.join()
    except KeyboardInterrupt:
        print("\n[DEBUG] 종료 중...")
        stop_event.set()
        redis_thread.join(timeout=5)
        update_thread.join(timeout=5)


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0] in ("--help", "-h"):
        print_usage()
        sys.exit(0)

    if args[0] == "debug":
        run_debug()
        sys.exit(0)

    # Windows 서비스 명령 처리 (install / start / stop / remove)
    try:
        import win32serviceutil
        from service import PCInspectService
        win32serviceutil.HandleCommandLine(PCInspectService)
    except ImportError:
        print("오류: pywin32가 설치되어 있지 않습니다.")
        print("pip install pywin32")
        sys.exit(1)

#
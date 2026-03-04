# main.py
# 진입점: exe 실행 시 자동 서비스 설치/시작, 또는 명령어로 관리

import sys


def print_usage():
    print("""
PC Inspect Client

사용법:
  PCInspectClient.exe           - 서비스 자동 설치 및 시작
  PCInspectClient.exe install   - 서비스 설치만
  PCInspectClient.exe start     - 서비스 시작
  PCInspectClient.exe stop      - 서비스 중지
  PCInspectClient.exe remove    - 서비스 제거
  PCInspectClient.exe status    - 서비스 상태 확인
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

    redis_thread = threading.Thread(target=subscribe_and_run, args=(stop_event,), daemon=True)
    update_thread = threading.Thread(target=check_and_update, args=(stop_event,), daemon=True)

    redis_thread.start()
    update_thread.start()

    try:
        redis_thread.join()
    except KeyboardInterrupt:
        print("\n[DEBUG] 종료 중...")
        stop_event.set()
        redis_thread.join(timeout=5)
        update_thread.join(timeout=5)


def auto_install_and_start():
    """exe 더블클릭 시 서비스 자동 설치 및 시작"""
    import config

    try:
        import win32serviceutil
        import win32service
        from service import PCInspectService
    except ImportError:
        print("오류: pywin32가 설치되어 있지 않습니다.")
        input("엔터를 누르면 종료됩니다...")
        sys.exit(1)

    print(f"PC Inspect Client v{config.CLIENT_VERSION}")
    print(f"서비스명: {config.SERVICE_NAME}")
    print()

    # 서비스 상태 확인
    try:
        status = win32serviceutil.QueryServiceStatus(config.SERVICE_NAME)[1]
        service_exists = True
    except Exception:
        service_exists = False
        status = None

    try:
        if not service_exists:
            print("서비스를 설치합니다...")
            win32serviceutil.HandleCommandLine(PCInspectService, argv=[sys.argv[0], "install"])
            print("서비스 설치 완료")

        if status != win32service.SERVICE_RUNNING:
            print("서비스를 시작합니다...")
            win32serviceutil.StartService(config.SERVICE_NAME)
            print("서비스 시작 완료")
        else:
            print("서비스가 이미 실행 중입니다.")

        print()
        print(f"✓ {config.SERVICE_DISPLAY} 정상 동작 중")

    except Exception as e:
        print(f"오류: {e}")
        print()
        print("관리자 권한으로 실행해주세요.")

    input("\n엔터를 누르면 종료됩니다...")


def run_as_service():
    """
    SCM(서비스 제어 관리자)이 인자 없이 exe를 시작할 때 호출.
    servicemanager.StartServiceCtrlDispatcher()로 SCM에 연결.
    SCM이 아닌 일반 실행이면 ERROR_FAILED_SERVICE_CONTROLLER_CONNECT(1063)
    예외가 발생하므로, 그때는 auto_install_and_start()로 폴백.
    """
    try:
        import servicemanager
        from service import PCInspectService
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(PCInspectService)
        servicemanager.StartServiceCtrlDispatcher()  # SCM 연결 — 서비스 종료까지 블로킹
    except Exception as e:
        if getattr(e, "winerror", None) == 1063:
            # ERROR_FAILED_SERVICE_CONTROLLER_CONNECT: 사용자가 직접 실행
            auto_install_and_start()
        else:
            # pywin32 없거나 기타 오류
            auto_install_and_start()


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        # 인자 없이 실행:
        #   - SCM이 서비스로 기동 → StartServiceCtrlDispatcher 호출
        #   - 사용자 더블클릭    → auto_install_and_start 폴백
        run_as_service()
        sys.exit(0)

    if args[0] in ("--help", "-h"):
        print_usage()
        sys.exit(0)

    if args[0] == "debug":
        run_debug()
        sys.exit(0)

    if args[0] == "status":
        try:
            import win32serviceutil
            import config
            status = win32serviceutil.QueryServiceStatus(config.SERVICE_NAME)
            state = {1: "중지됨", 2: "시작 중", 3: "중지 중", 4: "실행 중"}.get(status[1], "알 수 없음")
            print(f"{config.SERVICE_NAME}: {state}")
        except Exception as e:
            print(f"서비스 상태 확인 실패: {e}")
        sys.exit(0)

    # install / start / stop / remove 등 win32serviceutil 명령 처리
    try:
        import win32serviceutil
        from service import PCInspectService
        win32serviceutil.HandleCommandLine(PCInspectService)
    except ImportError:
        print("오류: pywin32가 설치되어 있지 않습니다.")
        sys.exit(1)

        #
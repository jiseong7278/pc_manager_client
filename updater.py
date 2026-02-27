# updater.py
# GitHub Releases에서 최신 exe 자동 다운로드 및 업데이트

import logging
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

import config

logger = logging.getLogger(__name__)


def _get_latest_release() -> dict | None:
    """GitHub API로 최신 릴리즈 정보 조회"""
    try:
        req = urllib.request.Request(
            config.GITHUB_API_URL,
            headers={"User-Agent": "PCInspectClient"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            import json
            return json.loads(resp.read().decode())
    except Exception as e:
        logger.warning(f"릴리즈 정보 조회 실패: {e}")
        return None


def _parse_version(tag: str) -> str:
    """v1.2.3 → 1.2.3"""
    return tag.lstrip("v")


def _is_newer(latest: str, current: str) -> bool:
    """버전 비교 (1.2.3 형식)"""
    try:
        def to_tuple(v):
            return tuple(int(x) for x in v.split("."))
        return to_tuple(latest) > to_tuple(current)
    except Exception:
        return False


def _find_exe_asset(assets: list) -> dict | None:
    """릴리즈 assets 중 .exe 파일 찾기"""
    for asset in assets:
        if asset.get("name", "").endswith(".exe"):
            return asset
    return None


def _do_update(download_url: str, new_version: str) -> None:
    """
    새 exe 다운로드 후 업데이트 스크립트 실행
    현재 프로세스를 새 exe로 교체하는 방식
    """
    current_exe = sys.executable if getattr(sys, "frozen", False) else None
    if not current_exe:
        logger.warning("스크립트 실행 모드에서는 자동 업데이트 미지원")
        return

    try:
        tmp_dir = tempfile.gettempdir()
        new_exe = os.path.join(tmp_dir, f"PCInspectClient_{new_version}.exe")

        logger.info(f"다운로드 중: {download_url}")
        urllib.request.urlretrieve(download_url, new_exe)
        logger.info(f"다운로드 완료: {new_exe}")

        bat_path    = os.path.join(tmp_dir, "pc_inspect_update.bat")
        current_dir = os.path.dirname(current_exe)
        target_exe  = os.path.join(current_dir, os.path.basename(current_exe))

        bat_content = f"""@echo off
timeout /t 3 /nobreak > nul
copy /Y "{new_exe}" "{target_exe}"
sc stop {config.SERVICE_NAME}
timeout /t 2 /nobreak > nul
sc start {config.SERVICE_NAME}
del "%~f0"
"""
        with open(bat_path, "w") as f:
            f.write(bat_content)

        subprocess.Popen(
            ["cmd", "/c", bat_path],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        logger.info("업데이트 스크립트 실행됨, 서비스 재시작 대기 중")

    except Exception as e:
        logger.error(f"업데이트 실패: {e}")


def trigger_update() -> None:
    """
    서버의 update 명령 수신 시 즉시 업데이트 실행
    주기적 확인 없이 바로 GitHub 릴리즈 확인 후 업데이트
    """
    logger.info("서버 명령으로 업데이트 시작")
    release = _get_latest_release()

    if not release:
        logger.warning("릴리즈 정보 없음, 업데이트 건너뜀")
        return

    latest_tag  = release.get("tag_name", "")
    latest_ver  = _parse_version(latest_tag)
    current_ver = config.CLIENT_VERSION
    assets      = release.get("assets", [])

    logger.info(f"현재 버전: {current_ver} / 최신 버전: {latest_ver}")

    if _is_newer(latest_ver, current_ver):
        asset = _find_exe_asset(assets)
        if asset:
            logger.info(f"새 버전 발견 ({latest_ver}), 업데이트 시작")
            _do_update(asset["browser_download_url"], latest_ver)
        else:
            logger.warning("릴리즈에 exe 파일 없음, 업데이트 건너뜀")
    else:
        logger.info(f"이미 최신 버전({current_ver}), 업데이트 불필요")


def check_and_update(stop_event) -> None:
    """주기적으로 GitHub Releases 확인 후 새 버전 있으면 업데이트"""
    while not stop_event.is_set():
        try:
            logger.info("업데이트 자동 확인 중...")
            trigger_update()
        except Exception as e:
            logger.error(f"업데이트 확인 중 오류: {e}")

        for _ in range(config.UPDATE_CHECK_INTERVAL):
            if stop_event.is_set():
                return
            time.sleep(1)
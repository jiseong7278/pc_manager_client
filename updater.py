# updater.py
# GitHub Releases에서 최신 MSI 자동 다운로드 및 업데이트

import logging
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

import truststore
truststore.inject_into_ssl()

import config

logger = logging.getLogger(__name__)


def _get_latest_release() -> dict | None:
    """GitHub API로 최신 릴리즈 정보 조회"""
    try:
        headers = {"User-Agent": "PCInspectClient"}
        if config.GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
        req = urllib.request.Request(config.GITHUB_API_URL, headers=headers)
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


def _find_msi_asset(assets: list) -> dict | None:
    """릴리즈 assets 중 .msi 파일 찾기"""
    for asset in assets:
        if asset.get("name", "").endswith(".msi"):
            return asset
    return None


def _do_update(download_url: str, new_version: str) -> None:
    """MSI 다운로드 후 msiexec으로 자동 설치"""
    if not getattr(sys, "frozen", False):
        logger.warning("스크립트 실행 모드에서는 자동 업데이트 미지원")
        return

    try:
        tmp_dir  = tempfile.gettempdir()
        msi_path = os.path.join(tmp_dir, f"PCInspectClient_{new_version}.msi")

        logger.info(f"다운로드 중: {download_url}")
        urllib.request.urlretrieve(download_url, msi_path)
        logger.info(f"다운로드 완료: {msi_path}")

        subprocess.Popen(
            ["msiexec", "/i", msi_path, "/qn", "/norestart"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        logger.info("MSI 설치 시작됨 (서비스가 자동으로 재시작됩니다)")

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
        asset = _find_msi_asset(assets)
        if asset:
            logger.info(f"새 버전 발견 ({latest_ver}), 업데이트 시작")
            _do_update(asset["browser_download_url"], latest_ver)
        else:
            logger.warning("릴리즈에 msi 파일 없음, 업데이트 건너뜀")
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
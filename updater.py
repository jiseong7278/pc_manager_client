# updater.py
# GitHub Releases에서 최신 MSI 자동 다운로드 및 업데이트

import hashlib
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

import truststore
truststore.inject_into_ssl()

import config

logger = logging.getLogger(__name__)

_update_lock  = threading.Lock()
_REGISTRY_KEY = r"SOFTWARE\PCInspector"


def _load_token_from_registry() -> None:
    """레지스트리에 저장된 GitHub Token을 읽어 config.GITHUB_TOKEN에 반영"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _REGISTRY_KEY) as key:
            token, _ = winreg.QueryValueEx(key, "GitHubToken")
            if token:
                config.GITHUB_TOKEN = token
                logger.debug("레지스트리에서 GitHub Token 로드 완료")
    except FileNotFoundError:
        pass  # 키 없음 — 정상 (미설정 상태)
    except Exception as e:
        logger.warning(f"레지스트리 GitHub Token 읽기 실패: {e}")


_load_token_from_registry()


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


def _verify_sha256(file_path: str, expected_digest: str) -> bool:
    """
    다운로드한 파일의 SHA-256을 계산해 GitHub API digest 값과 비교.
    expected_digest 형식: "sha256:<hexhash>" (GitHub API assets[].digest)
    """
    if not expected_digest.startswith("sha256:"):
        logger.warning(f"지원하지 않는 digest 형식: {expected_digest!r} — 검증 건너뜀")
        return True  # 알 수 없는 형식은 차단하지 않음

    expected_hash = expected_digest.split(":", 1)[1].lower()
    try:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256.update(chunk)
        actual_hash = sha256.hexdigest().lower()

        if actual_hash != expected_hash:
            logger.error(f"SHA-256 불일치: expected={expected_hash} actual={actual_hash}")
            return False
        logger.info("SHA-256 검증 성공")
        return True
    except Exception as e:
        logger.error(f"SHA-256 검증 실패: {e}")
        return False


def _download_msi(url: str, dest: str) -> None:
    """
    MSI 파일 다운로드.
    토큰이 있으면 GitHub API URL + Bearer 인증으로 다운로드 (비공개 저장소 지원).
    토큰이 없으면 browser_download_url을 직접 사용.
    """
    headers = {"User-Agent": "PCInspectClient"}
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
        headers["Accept"]        = "application/octet-stream"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)


def _do_update(download_url: str, new_version: str, digest: str = "", api_url: str = "") -> None:
    """MSI 다운로드 → SHA-256 검증 (digest 있을 때) → msiexec으로 자동 설치"""
    if not getattr(sys, "frozen", False):
        logger.warning("스크립트 실행 모드에서는 자동 업데이트 미지원")
        return

    try:
        tmp_dir  = tempfile.gettempdir()
        msi_path = os.path.join(tmp_dir, f"PCInspectClient_{new_version}.msi")

        # 토큰이 있으면 API URL(비공개 저장소 가능), 없으면 browser URL(공개 저장소)
        effective_url = api_url if (api_url and config.GITHUB_TOKEN) else download_url
        logger.info(f"다운로드 중: {effective_url}")
        _download_msi(effective_url, msi_path)
        logger.info(f"다운로드 완료: {msi_path}")

        if digest:
            if not _verify_sha256(msi_path, digest):
                logger.error("무결성 검증 실패 — 업데이트 중단")
                try:
                    os.remove(msi_path)
                except Exception:
                    pass
                return
        else:
            logger.warning("digest 없음 — 무결성 검증 건너뜀")

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
    if not _update_lock.acquire(blocking=False):
        logger.info("업데이트가 이미 진행 중입니다. 건너뜁니다.")
        return
    try:
        _do_trigger_update()
    finally:
        _update_lock.release()


def _do_trigger_update() -> None:
    logger.info("업데이트 시작")
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
            digest = asset.get("digest", "")  # GitHub API가 자동 생성 (예: "sha256:abc123...")
            _do_update(
                asset["browser_download_url"],
                latest_ver,
                digest,
                api_url=asset.get("url", ""),  # 비공개 저장소용 API URL
            )
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
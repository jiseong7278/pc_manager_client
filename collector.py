# collector.py
# PC 스펙 및 보안 프로그램 정보 수집

import json
import logging
import platform
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))

logger = logging.getLogger(__name__)


# ── 보안 프로그램 수집 ────────────────────────────────────────────
def get_antivirus_info() -> dict:
    """WMI SecurityCenter2로 백신 탐지, 알약/V3/Defender 버전 수집"""
    try:
        ps_script = """
        $av = Get-WmiObject -Namespace root/SecurityCenter2 -Class AntiVirusProduct 2>$null
        if ($av) {
            $av | ForEach-Object {
                [PSCustomObject]@{
                    name          = $_.displayName
                    product_state = $_.productState
                    path          = $_.pathToSignedProductExe
                }
            } | ConvertTo-Json -Compress
        } else {
            Write-Output "[]"
        }
        """
        result = _run_powershell(ps_script)
        av_list = _parse_json(result, [])
        if isinstance(av_list, dict):
            av_list = [av_list]

        programs = []
        for av in av_list:
            name    = av.get("name", "")
            state   = av.get("product_state", 0)
            enabled = _parse_av_state(state)

            info = {
                "name":    name,
                "enabled": enabled,
                "version": None,
                "type":    _detect_av_type(name),
            }

            if info["type"] == "defender":
                info.update(_get_defender_version())
            elif info["type"] == "alyac":
                info["version"] = _get_registry_version(
                    r"SOFTWARE\ESTsoft\ALYac", "Version"
                ) or _get_registry_version(
                    r"SOFTWARE\WOW6432Node\ESTsoft\ALYac", "Version"
                )
            elif info["type"] == "v3":
                info["version"] = _get_registry_version(
                    r"SOFTWARE\AhnLab\V3 365 Clinic", "Version"
                ) or _get_registry_version(
                    r"SOFTWARE\WOW6432Node\AhnLab\V3 365 Clinic", "Version"
                )

            programs.append(info)

        if not programs:
            return {"status": "no_av", "programs": [], "message": "감지된 백신 없음"}

        return {"status": "active", "programs": programs}

    except Exception as e:
        logger.error(f"백신 정보 수집 실패: {e}")
        return {"status": "error", "programs": [], "message": str(e)}


def _detect_av_type(name: str) -> str:
    name_lower = name.lower()
    if "alyac" in name_lower or "알약" in name_lower or "estsoft" in name_lower:
        return "alyac"
    if "v3" in name_lower or "ahnlab" in name_lower:
        return "v3"
    if "windows defender" in name_lower or "microsoft defender" in name_lower:
        return "defender"
    return "other"


def _parse_av_state(state: int) -> bool:
    """productState 값에서 활성화 여부 파싱 (0x1000 비트가 활성화)"""
    try:
        return (int(state) & 0x1000) != 0
    except Exception:
        return False


def _get_defender_version() -> dict:
    """Windows Defender 엔진/정의 버전 수집"""
    try:
        ps_script = """
        $status = Get-MpComputerStatus 2>$null
        if ($status) {
            [PSCustomObject]@{
                product_version        = $status.AMProductVersion
                engine_version         = $status.AMEngineVersion
                signature_version      = $status.AntivirusSignatureVersion
                real_time              = $status.RealTimeProtectionEnabled
                signatures_out_of_date = $status.DefenderSignaturesOutOfDate
            } | ConvertTo-Json -Compress
        }
        """
        result = _run_powershell(ps_script)
        data = _parse_json(result, {})
        out_of_date = data.get("signatures_out_of_date")
        is_up_to_date = (not out_of_date) if out_of_date is not None else None
        return {
            "version":           data.get("product_version"),
            "engine_version":    data.get("engine_version"),
            "signature_version": data.get("signature_version"),
            "real_time":         data.get("real_time", False),
            "is_up_to_date":     is_up_to_date,
        }
    except Exception as e:
        logger.warning(f"Defender 버전 수집 실패: {e}")
        return {"version": None}


def _get_registry_version(key_path: str, value_name: str) -> str | None:
    """레지스트리에서 버전 정보 읽기"""
    try:
        ps_script = f"""
        $val = Get-ItemPropertyValue -Path 'HKLM:\\{key_path}' -Name '{value_name}' 2>$null
        if ($val) {{ Write-Output $val }}
        """
        result = _run_powershell(ps_script).strip()
        return result if result else None
    except Exception:
        return None


# ── PC 스펙 수집 ──────────────────────────────────────────────────
def get_hardware_info() -> dict:
    """CPU, RAM, 디스크, OS, MAC, 컴퓨터 이름 수집 (병렬 실행)"""
    tasks = {
        "mac_address": _get_mac_address,
        "os":          _get_os_info,
        "cpu":         _get_cpu_info,
        "gpu":         _get_gpu_info,
        "ram":         _get_ram_info,
        "disks":       _get_disk_info,
    }
    results = {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
        futures = {ex.submit(fn): key for key, fn in tasks.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as e:
                logger.warning(f"hardware.{key} 수집 실패: {e}")
                results[key] = {"error": str(e)}
    results["computer_name"] = platform.node()
    return results


def _get_mac_address() -> str:
    """MAC 주소 수집 (물리적 NIC 기준)"""
    try:
        ps_script = """
        Get-NetAdapter | Where-Object { $_.Status -eq 'Up' -and $_.MacAddress -ne '' } |
        Select-Object -First 1 -ExpandProperty MacAddress
        """
        result = _run_powershell(ps_script).strip()
        return result if result else str(uuid.getnode())
    except Exception:
        return str(uuid.getnode())


def _get_os_info() -> dict:
    try:
        ps_script = """
        $os = Get-WmiObject Win32_OperatingSystem
        [PSCustomObject]@{
            name         = $os.Caption
            version      = $os.Version
            build        = $os.BuildNumber
            architecture = $os.OSArchitecture
        } | ConvertTo-Json -Compress
        """
        data = _parse_json(_run_powershell(ps_script), {})
        return {
            "name":         data.get("name", platform.system()),
            "version":      data.get("version", platform.version()),
            "build":        data.get("build"),
            "architecture": data.get("architecture", platform.machine()),
        }
    except Exception as e:
        logger.warning(f"OS 정보 수집 실패: {e}")
        return {"name": platform.system(), "version": platform.version(), "error": str(e)}


def _get_cpu_info() -> dict:
    try:
        ps_script = """
        $cpu = Get-WmiObject Win32_Processor | Select-Object -First 1
        [PSCustomObject]@{
            name         = $cpu.Name.Trim()
            cores        = $cpu.NumberOfCores
            logical_cpus = $cpu.NumberOfLogicalProcessors
            max_clock    = $cpu.MaxClockSpeed
        } | ConvertTo-Json -Compress
        """
        data = _parse_json(_run_powershell(ps_script), {})
        return {
            "name":          data.get("name", "Unknown"),
            "cores":         data.get("cores"),
            "logical_cpus":  data.get("logical_cpus"),
            "max_clock_mhz": data.get("max_clock"),
        }
    except Exception as e:
        logger.warning(f"CPU 정보 수집 실패: {e}")
        return {"name": "Unknown", "error": str(e)}


def _get_gpu_info() -> list:
    try:
        ps_script = """
        Get-WmiObject Win32_VideoController |
        Select-Object Name, AdapterRAM, DriverVersion |
        ConvertTo-Json -Compress
        """
        result = _run_powershell(ps_script)
        data = _parse_json(result, [])
        if isinstance(data, dict):
            data = [data]
        return [
            {
                "name":           g.get("Name", "Unknown"),
                "vram_bytes":     g.get("AdapterRAM"),
                "driver_version": g.get("DriverVersion"),
            }
            for g in data
        ]
    except Exception as e:
        logger.warning(f"GPU 정보 수집 실패: {e}")
        return []


def _get_ram_info() -> dict:
    try:
        ps_script = """
        $os = Get-WmiObject Win32_OperatingSystem
        [PSCustomObject]@{
            total_bytes     = $os.TotalVisibleMemorySize * 1KB
            available_bytes = $os.FreePhysicalMemory * 1KB
        } | ConvertTo-Json -Compress
        """
        data = _parse_json(_run_powershell(ps_script), {})
        total = data.get("total_bytes", 0)
        avail = data.get("available_bytes", 0)
        return {
            "total_gb":     round(total / (1024**3), 2) if total else None,
            "available_gb": round(avail / (1024**3), 2) if avail else None,
        }
    except Exception as e:
        logger.warning(f"RAM 정보 수집 실패: {e}")
        return {"error": str(e)}


def _get_disk_info() -> list:
    try:
        ps_script = """
        Get-PhysicalDisk | Select-Object FriendlyName, MediaType, Size, HealthStatus |
        ConvertTo-Json -Compress
        """
        result = _run_powershell(ps_script)
        data = _parse_json(result, [])
        if isinstance(data, dict):
            data = [data]
        disks = []
        for d in data:
            size = d.get("Size", 0)
            disks.append({
                "name":          d.get("FriendlyName", "Unknown"),
                "type":          d.get("MediaType", "Unknown"),
                "size_gb":       round(int(size) / (1024**3), 2) if size else None,
                "health_status": d.get("HealthStatus", "Unknown"),
            })
        return disks
    except Exception as e:
        logger.warning(f"디스크 정보 수집 실패: {e}")
        return []


# 전체 수집 최대 대기 시간 (초). PowerShell 개별 timeout=30 * 여러 단계 고려
_COLLECT_TIMEOUT = 90


# ── 전체 데이터 통합 ──────────────────────────────────────────────
def collect_all() -> dict:
    """
    PC 전체 데이터 수집
    hostname, ip_address는 호출측(redis_client.py)에서 추가
    """
    import config
    logger.info("PC 데이터 수집 시작")

    # 백신/하드웨어 수집을 병렬로 실행
    # with 컨텍스트 매니저는 __exit__에서 무한 대기하므로 직접 제어
    ex = ThreadPoolExecutor(max_workers=2)
    try:
        fut_av = ex.submit(get_antivirus_info)
        fut_hw = ex.submit(get_hardware_info)

        try:
            antivirus = fut_av.result(timeout=_COLLECT_TIMEOUT)
        except FuturesTimeoutError:
            logger.error("백신 정보 수집 타임아웃 (%ds 초과)", _COLLECT_TIMEOUT)
            antivirus = {"status": "error", "programs": [], "message": f"수집 타임아웃 ({_COLLECT_TIMEOUT}s)"}

        try:
            hardware = fut_hw.result(timeout=_COLLECT_TIMEOUT)
        except FuturesTimeoutError:
            logger.error("하드웨어 정보 수집 타임아웃 (%ds 초과)", _COLLECT_TIMEOUT)
            hardware = {"computer_name": platform.node(), "error": f"수집 타임아웃 ({_COLLECT_TIMEOUT}s)"}
    finally:
        ex.shutdown(wait=False)  # 타임아웃된 스레드는 백그라운드에서 자연 종료 대기

    # 각 섹션의 오류 집계
    errors = []
    if antivirus.get("status") == "error":
        errors.append({"section": "antivirus", "message": antivirus.get("message", "")})
    # 하드웨어 전체 타임아웃 (최상위 error 키)
    if hardware.get("error"):
        errors.append({"section": "hardware", "message": hardware["error"]})
    else:
        for section in ("os", "cpu", "ram"):
            err = hardware.get(section, {}).get("error")
            if err:
                errors.append({"section": f"hardware.{section}", "message": err})

    data = {
        "collected_at":   datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S"),
        "client_version": config.CLIENT_VERSION,
        "antivirus":      antivirus,
        "hardware":       hardware,
    }
    if errors:
        data["collection_errors"] = errors
        logger.warning(f"수집 오류 {len(errors)}건: {[e['section'] for e in errors]}")

    logger.info("PC 데이터 수집 완료")
    return data


# ── 백신 업데이트 ─────────────────────────────────────────────────
def trigger_av_update() -> dict:
    """
    설치된 백신의 정의(시그니처)를 업데이트한다.

    반환값: {"updated": [백신명, ...], "skipped": [백신명, ...], "errors": [메시지, ...]}
    """
    av_info = get_antivirus_info()
    programs: list[dict] = av_info.get("programs") or []

    updated = []
    skipped = []
    errors  = []

    if not programs:
        logger.info("av_update: 설치된 백신 없음")
        return {"updated": [], "skipped": [], "errors": ["설치된 백신 없음"]}

    for av in programs:
        av_type = av.get("type", "")
        av_name = av.get("name", av_type)

        if av_type == "defender":
            try:
                _run_powershell("Update-MpSignature")
                logger.info("Defender 백신 정의 업데이트 완료")
                updated.append(av_name)
            except Exception as e:
                logger.error("Defender 업데이트 실패: %s", e)
                errors.append(f"{av_name}: {e}")

        elif av_type == "alyac":
            import os
            # 레지스트리에서 설치 경로 탐색 후 기본 경로를 폴백으로 추가
            update_paths = []
            reg_dir = _find_av_install_dir(["ALYac", "알약"])
            if reg_dir:
                update_paths.append(os.path.join(reg_dir, "AYUpdate.aye"))
            update_paths += [
                r"C:\Program Files (x86)\ESTsoft\ALYac\AYUpdate.aye",
                r"C:\Program Files\ESTsoft\ALYac\AYUpdate.aye",
            ]
            ran = False
            for path in update_paths:
                if os.path.exists(path):
                    if not _is_allowed_av_path(path):
                        logger.error("ALYac 업데이트 거부: 허용 디렉터리 외 경로 %s", path)
                        errors.append(f"{av_name}: 허용되지 않은 경로")
                        break
                    try:
                        os.startfile(path)
                        logger.info("ALYac 업데이트 실행: %s", path)
                        updated.append(av_name)
                        ran = True
                        break
                    except Exception as e:
                        errors.append(f"{av_name}: {e}")
            if not ran and not any(av_name in e for e in errors):
                logger.warning("ALYac 업데이트 실행 파일 없음")
                skipped.append(av_name)

        elif av_type == "v3":
            import os
            # 레지스트리에서 설치 경로 탐색 후 기본 경로를 폴백으로 추가
            update_paths = []
            reg_dir = _find_av_install_dir(["V3 365 Clinic"])
            if reg_dir:
                update_paths.append(os.path.join(reg_dir, "V3Svc.exe"))
            update_paths += [
                r"C:\Program Files (x86)\AhnLab\V3 365 Clinic\V3Svc.exe",
                r"C:\Program Files\AhnLab\V3 365 Clinic\V3Svc.exe",
            ]
            ran = False
            for path in update_paths:
                if os.path.exists(path):
                    if not _is_allowed_av_path(path):
                        logger.error("V3 업데이트 거부: 허용 디렉터리 외 경로 %s", path)
                        errors.append(f"{av_name}: 허용되지 않은 경로")
                        break
                    try:
                        # 단일 인용 문자열 사용 — PowerShell 변수 치환/이스케이프 방지
                        escaped = path.replace("'", "''")
                        _run_powershell(f"Start-Process -LiteralPath '{escaped}' -ArgumentList '/update' -WindowStyle Hidden")
                        logger.info("V3 업데이트 실행: %s", path)
                        updated.append(av_name)
                        ran = True
                        break
                    except Exception as e:
                        errors.append(f"{av_name}: {e}")
            if not ran and not any(av_name in e for e in errors):
                logger.warning("V3 업데이트 실행 파일 없음")
                skipped.append(av_name)

        else:
            logger.debug("av_update: 지원하지 않는 백신 타입 %r, 건너뜀", av_type)
            skipped.append(av_name)

    return {"updated": updated, "skipped": skipped, "errors": errors}


# ── 유틸 ──────────────────────────────────────────────────────────

# 백신 실행 파일 허용 부모 디렉터리 — 이 범위 밖의 경로는 실행 거부
_ALLOWED_AV_DIRS: frozenset[str] = frozenset({
    r"C:\Program Files (x86)\ESTsoft",
    r"C:\Program Files\ESTsoft",
    r"C:\Program Files (x86)\AhnLab",
    r"C:\Program Files\AhnLab",
})


def _is_allowed_av_path(path: str) -> bool:
    """경로가 허용된 백신 설치 디렉터리 내에 있는지 검증"""
    import os
    norm = os.path.normcase(os.path.normpath(path))
    return any(
        norm.startswith(os.path.normcase(os.path.normpath(d)) + os.sep)
        for d in _ALLOWED_AV_DIRS
    )


def _find_av_install_dir(display_name_keywords: list[str]) -> str | None:
    """언인스톨 레지스트리에서 백신 설치 디렉터리 탐색.
    설치된 앱 목록을 순회하며 DisplayName이 키워드를 포함하는 항목의
    InstallLocation을 반환한다."""
    import os
    try:
        import winreg
    except ImportError:
        return None

    search_keys = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ]
    for key_path in search_keys:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as root:
                i = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(root, i)
                        with winreg.OpenKey(root, sub_name) as sub:
                            try:
                                name, _ = winreg.QueryValueEx(sub, "DisplayName")
                                if any(kw.lower() in name.lower() for kw in display_name_keywords):
                                    try:
                                        loc, _ = winreg.QueryValueEx(sub, "InstallLocation")
                                        if loc and os.path.isdir(loc):
                                            return loc
                                    except FileNotFoundError:
                                        pass
                            except FileNotFoundError:
                                pass
                        i += 1
                    except OSError:
                        break
        except Exception:
            pass
    return None


def _run_powershell(script: str) -> str:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30
    )
    return result.stdout.strip()


def _parse_json(text: str, default):
    try:
        return json.loads(text)
    except Exception:
        return default
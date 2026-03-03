# collector.py
# PC 스펙 및 보안 프로그램 정보 수집

import json
import logging
import platform
import subprocess
import uuid
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
                product_version   = $status.AMProductVersion
                engine_version    = $status.AMEngineVersion
                signature_version = $status.AntivirusSignatureVersion
                real_time         = $status.RealTimeProtectionEnabled
            } | ConvertTo-Json -Compress
        }
        """
        result = _run_powershell(ps_script)
        data = _parse_json(result, {})
        return {
            "version":           data.get("product_version"),
            "engine_version":    data.get("engine_version"),
            "signature_version": data.get("signature_version"),
            "real_time":         data.get("real_time", False),
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
    """CPU, RAM, 디스크, OS, MAC, 컴퓨터 이름 수집"""
    return {
        "mac_address":   _get_mac_address(),
        "computer_name": platform.node(),
        "os":            _get_os_info(),
        "cpu":           _get_cpu_info(),
        "gpu":           _get_gpu_info(),
        "ram":           _get_ram_info(),
        "disks":         _get_disk_info(),
    }


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
        return {"name": platform.system(), "version": platform.version()}


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
        return {"name": "Unknown"}


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
        return {}


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


# ── 전체 데이터 통합 ──────────────────────────────────────────────
def collect_all() -> dict:
    """
    PC 전체 데이터 수집
    hostname, ip_address는 호출측(redis_client.py)에서 추가
    """
    import config
    logger.info("PC 데이터 수집 시작")
    data = {
        "collected_at":   datetime.now(KST).isoformat(),
        "client_version": config.CLIENT_VERSION,
        "antivirus":      get_antivirus_info(),
        "hardware":       get_hardware_info(),
    }
    logger.info("PC 데이터 수집 완료")
    return data


# ── 유틸 ──────────────────────────────────────────────────────────
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
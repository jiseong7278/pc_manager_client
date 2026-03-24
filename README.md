# pc_manager_client

`pc_inspector` 시스템의 Windows PC 에이전트입니다. Windows 서비스로 상시 실행되며, 서버 명령에 따라 PC 정보를 수집·보고하고 자기 자신을 자동으로 업데이트합니다.

---

## 기술 스택

| 항목 | 내용 |
|---|---|
| 언어 | Python 3.11+ |
| 실행 방식 | Windows 서비스 (pywin32 `ServiceFramework`) |
| 메시지 브로커 클라이언트 | redis-py (Pub-Sub 구독 + Stream 전송) |
| WebSocket 클라이언트 | websocket-client (heartbeat 전송) |
| 하드웨어 수집 | PowerShell subprocess (WMI, Get-NetAdapter 등) |
| 빌드 | PyInstaller (onedir) |
| 배포 | WiX Toolset v4 MSI 인스톨러 |
| 자동 업데이트 | GitHub Releases API + msiexec 설치 |
| 보안 | HMAC-SHA256 명령 서명 검증, 레지스트리 시크릿 저장 |
| TLS | truststore (시스템 인증서 활용) |
| 테스트 | pytest, unittest.mock |

---

## 주요 기능

### PC 정보 수집 (`collector.py`)
`inspect` 명령 수신 시 PowerShell을 통해 PC 전체 정보를 수집합니다.

| 분류 | 수집 항목 |
|---|---|
| 식별 | MAC 주소, 컴퓨터 이름, IP 주소 |
| OS | 이름, 버전, 빌드 번호, 아키텍처 |
| CPU | 모델명, 코어 수, 논리 CPU 수, 최대 클럭 |
| GPU | 모델명, VRAM, 드라이버 버전 |
| RAM | 전체 용량, 사용 가능 용량 |
| 디스크 | 모델명, 종류(SSD/HDD), 용량, 건강 상태 |
| 백신 | Windows Defender / V3 / 알약 — 활성화 여부, 버전, 실시간 보호 상태 |

각 수집 단계는 독립적으로 try/except 처리되어 일부 실패 시에도 나머지 데이터를 보고합니다. 오류 내역은 보고서의 `collection_errors` 배열에 포함됩니다.

### Redis Pub-Sub 명령 수신 (`redis_client.py`)
`pc_inspect` 채널을 구독해 서버 명령을 처리합니다.

| 명령 | 동작 |
|---|---|
| `inspect` | 데이터 수집 후 Redis Stream(`pc_reports`)에 전송 |
| `update` | GitHub Releases에서 최신 MSI 다운로드 → 자동 설치 |
| `set_token` | GitHub Token을 레지스트리(`HKLM\SOFTWARE\PCInspector\GitHubToken`)에 저장 |
| `set_secret` | HMAC 시크릿을 레지스트리(`HKLM\SOFTWARE\PCInspector\HMACSecret`)에 저장 |

`target` / `targets` 필드로 대상 PC가 지정된 경우, 호스트명이 일치할 때만 실행합니다.

### Heartbeat 전송 (`redis_client.py`)
60초마다 WebSocket(`/ws/client?device_type=pc`)으로 heartbeat를 전송해 서버가 온라인 상태를 추적할 수 있게 합니다. WebSocket 연결이 끊기면 점진적 백오프(5s → 10s → 30s → 60s → 120s)로 재연결을 시도합니다.

### 자동 업데이트 (`updater.py`)
`UPDATE_CHECK_INTERVAL`초(기본 300초)마다, 또는 `update` 명령 수신 즉시 GitHub Releases API를 조회합니다.

1. 현재 버전보다 높은 릴리즈 발견 시 `.msi` 에셋 다운로드
2. SHA-256 해시 검증 (`digest` 필드 있는 경우)
3. `msiexec /i /qn` 무인 설치 → 서비스 자동 재시작

Private 저장소인 경우 레지스트리에 저장된 GitHub Token(`Bearer` 헤더)으로 다운로드합니다.

### HMAC 명령 서명 검증
레지스트리에 HMAC 시크릿이 설정된 경우, 수신한 명령의 `sig` 필드를 검증합니다. `set_secret` / `set_token` 명령은 부트스트랩 목적이므로 검증을 면제합니다.

### 실패 보고서 로컬 캐싱
Redis Stream 전송 실패 시 `failed_reports.json`에 저장하고, 다음 연결 성공 시 자동으로 재전송합니다.

---

## 디렉터리 구조

```
pc_manager_client/
├── main.py              # 진입점 (서비스 관리 명령 / debug 모드)
├── service.py           # Windows 서비스 정의 (PCInspectService)
├── collector.py         # PC 하드웨어 + 백신 정보 수집
├── redis_client.py      # Redis Pub-Sub 수신 + Stream 전송 + Heartbeat WS
├── updater.py           # GitHub Releases 자동 업데이트
├── config.py            # 서버 주소 등 설정 (배포 전 수정 필요)
├── build.spec           # PyInstaller 빌드 설정 (onedir)
├── generate_wxs.py      # WiX MSI 컴포넌트 파일 자동 생성
├── requirements.txt
└── tests/
    ├── test_redis_client.py
    └── test_updater.py
```

---

## 설정 (`config.py`)

배포 전 수정이 필요한 값:

```python
REDIS_HOST     = "서버호스트명"               # Redis 서버 주소
REDIS_PORT     = 6379
REDIS_PASSWORD = ""                            # CI/CD에서 자동 주입
GITHUB_REPO    = "owner/pc_manager_client"    # GitHub 저장소 (자동 업데이트용)
GITHUB_TOKEN   = ""                            # CI/CD에서 자동 주입
```

`REDIS_PASSWORD`와 `GITHUB_TOKEN`은 `config.py`에 직접 입력하지 않습니다. GitHub Actions 빌드 파이프라인이 Secrets에서 자동으로 주입합니다.

---

## 설치 및 관리

관리자 권한 PowerShell에서 실행합니다.

```powershell
# EXE 실행 시 서비스 자동 설치 + 시작
.\PCInspectClient.exe

# 개별 명령
.\PCInspectClient.exe install   # 서비스 설치
.\PCInspectClient.exe start     # 서비스 시작
.\PCInspectClient.exe stop      # 서비스 중지
.\PCInspectClient.exe remove    # 서비스 제거
.\PCInspectClient.exe status    # 서비스 상태 확인
.\PCInspectClient.exe debug     # 서비스 없이 직접 실행 (개발용)
```

---

## 빌드 및 패키징

```bash
# 의존성 설치
pip install -r requirements.txt

# exe 빌드 (onedir)
pyinstaller build.spec
# 출력: dist/PCInspectClient/PCInspectClient.exe

# WiX 컴포넌트 파일 생성 (MSI 패키징용)
python generate_wxs.py
# 출력: dist_files.wxs
```

---

## 테스트

```bash
pip install -r requirements-test.txt
pytest tests/ -v
```

`win32serviceutil`, `win32service`, `redis` 등 Windows 전용 모듈은 Mock 처리되므로 비-Windows 환경에서도 실행 가능합니다.

---

## Redis 재연결 백오프

Redis 연결 실패 시 다음 순서로 재연결을 시도합니다:

```
5초 → 10초 → 30초 → 60초 → 120초 (이후 120초 유지)
```

연결 성공 시 카운터가 초기화됩니다. Heartbeat WebSocket도 동일한 백오프 전략을 사용합니다.

---

## 로그

서비스 로그는 `logs/client.log`에 일별 로테이션(보관 30일)으로 기록됩니다.

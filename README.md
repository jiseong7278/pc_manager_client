# PC Inspect Client

사내 PC 일괄 점검 시스템의 **Windows 에이전트**. Windows 서비스로 상시 실행되며, 서버 명령에 따라 PC 정보를 수집·보고하고 자동으로 자기 자신을 업데이트한다.

---

## 전체 아키텍처에서의 위치

```
[pc_manager_server]
       │  Redis Pub-Sub (pc_inspect 채널)
       ▼
[pc_manager_client]          ← 이 컴포넌트 (각 PC에 설치)
       │  inspect 명령 수신 → PC 정보 수집
       │  Redis Stream (pc_reports 키)
       ▼
[pc_manager_server]
```

---

## 디렉토리 구조

```
pc_manager_client/
├── main.py              # 진입점 (서비스 관리 / debug 모드)
├── service.py           # Windows 서비스 (PCInspectService)
├── collector.py         # PC 정보 수집 (하드웨어 + 백신)
├── redis_client.py      # Redis Pub-Sub 수신 + Stream 전송
├── updater.py           # GitHub Releases 자동 업데이트
├── config.py            # 서버 주소 등 설정 (배포 전 수정 필요)
├── build.spec           # PyInstaller 빌드 설정
├── generate_wxs.py      # WiX MSI 컴포넌트 파일 생성
├── requirements.txt
└── tests/
    ├── test_redis_client.py
    └── test_updater.py
```

---

## 설정

배포 전 `config.py`를 열어 환경에 맞게 수정한다.

```python
REDIS_HOST     = "it-team"                    # Redis 서버 호스트명 또는 IP
REDIS_PORT     = 6379
REDIS_PASSWORD = ""                            # 빌드 시 GitHub Secrets에서 자동 주입
GITHUB_REPO    = "owner/pc_manager_client"    # GitHub 저장소 (자동 업데이트용)
GITHUB_TOKEN   = ""                            # 빌드 시 GitHub Secrets에서 자동 주입
```

> **주의**: `REDIS_PASSWORD`와 `GITHUB_TOKEN`은 `config.py`에 직접 입력하지 않는다. GitHub Actions 빌드 파이프라인(`release.yml`)이 `secrets.REDIS_PASSWORD`와 `secrets.GITHUB_TOKEN`을 자동으로 주입한다.

> `config.py`는 PyInstaller 빌드 시 `dist/PCInspectClient/config.py`로 복사된다. 빌드 후에도 해당 파일을 직접 편집할 수 있다.

---

## 개발 환경 실행

```bash
pip install -r requirements.txt

# 서비스 없이 직접 실행 (개발/테스트용)
python main.py debug
```

debug 모드는 Windows 서비스를 설치하지 않고 Redis 구독과 자동 업데이트를 메인 스레드에서 직접 실행한다. `Ctrl+C`로 종료.

---

## 테스트

```bash
pip install -r requirements-test.txt
pytest tests/ -v
```

`win32serviceutil`, `win32service`, `redis` 등 Windows 전용 모듈은 Mock 처리되므로 비-Windows 환경에서도 실행 가능.

---

## 빌드 (exe)

```bash
pip install pyinstaller
pyinstaller build.spec
```

빌드 결과: `dist/PCInspectClient/PCInspectClient.exe`

onedir 모드로 빌드된다. `dist/PCInspectClient/` 디렉토리 전체가 배포 단위이며, `config.py`가 함께 포함된다.

---

## MSI 설치 파일 생성

```bash
# exe 빌드 후 실행
python generate_wxs.py
# 결과: dist_files.wxs (WiX Toolset로 MSI 패키징에 사용)
```

WiX Toolset으로 MSI를 생성하면 표준 Windows 설치 프로그램으로 배포할 수 있다. MSI 설치 시 이전 버전은 자동으로 제거된다.

---

## Windows 서비스 설치 및 관리

관리자 권한 PowerShell에서 실행한다.

```powershell
# 서비스 설치 + 시작 (exe 더블클릭 또는)
.\PCInspectClient.exe

# 개별 명령
.\PCInspectClient.exe install   # 서비스 설치
.\PCInspectClient.exe start     # 서비스 시작
.\PCInspectClient.exe stop      # 서비스 중지
.\PCInspectClient.exe remove    # 서비스 제거
.\PCInspectClient.exe status    # 서비스 상태 확인
.\PCInspectClient.exe debug     # 서비스 없이 직접 실행
```

---

## 수집 데이터

`collector.py`가 PowerShell을 통해 수집하는 항목:

| 분류 | 항목 |
|------|------|
| 식별 | MAC 주소, 컴퓨터 이름, IP 주소 |
| OS | 이름, 버전, 빌드 번호, 아키텍처 |
| CPU | 모델명, 코어 수, 논리 CPU 수, 최대 클럭 |
| GPU | 모델명, VRAM, 드라이버 버전 |
| RAM | 전체 용량 |
| 디스크 | 모델명, 종류(SSD/HDD), 용량, 상태 |
| 백신 | Windows Defender, V3, 알약 — 활성화 여부, 버전, 실시간 보호 상태 |

PowerShell 명령 실패 시 해당 섹션에 `error` 필드가 추가되고, 보고서의 `collection_errors` 배열에 오류 내역이 집계된다. 관리자 UI에서 해당 행이 회색으로 표시되며 오류 항목을 툴팁으로 확인할 수 있다.

---

## Redis 재연결 정책

Redis 연결이 끊기면 다음 순서로 재연결을 시도한다:

```
5초 → 10초 → 30초 → 60초 → 120초 (이후 120초 유지)
```

연결 성공 시 카운터가 초기화되어 다음 장애 시 5초부터 다시 시작한다.

연결 중 수집된 보고서가 전송 실패한 경우 `failed_reports.json`에 저장되며, 다음 heartbeat 성공 시 자동으로 재전송한다.

---

## 자동 업데이트 동작

1. `UPDATE_CHECK_INTERVAL`초(기본 300초)마다 GitHub Releases API 조회
2. 현재 버전보다 높은 버전에 `.msi` 또는 `PCInspectClient.exe` 에셋 존재 시 다운로드
3. Private 저장소인 경우 GitHub Token을 Bearer 인증 헤더로 사용해 다운로드
4. 배치 스크립트로 서비스 중지 → exe 교체 → 서비스 재시작

GitHub Releases에 `vX.Y.Z` 태그와 에셋을 포함한 릴리즈를 게시하면 자동으로 전체 배포된다.

---

## 보안

### HMAC 명령 서명 검증
`HMAC_SECRET`이 설정된 경우(레지스트리 `HKLM\SOFTWARE\PCInspector\HmacSecret`) 수신한 Pub-Sub 명령의 `sig` 필드를 HMAC-SHA256으로 검증한다. 서명 불일치 시 명령을 무시한다.

`set_secret` / `set_token` 명령은 시크릿 배포 자체가 목적이므로 검증을 면제한다.

### 시크릿 배포 흐름
1. 관리자가 서버에 `set_secret` 명령 전송
2. 서버가 Redis Pub-Sub으로 모든 클라이언트에 전달
3. 각 클라이언트가 레지스트리(`HmacSecret`)에 저장
4. 이후 수신하는 모든 명령에 서명 검증 적용

### GitHub Token 배포 흐름
1. 관리자 UI 설정 다이얼로그에서 Token 입력 후 "토큰 배포"
2. 서버가 `set_token` 명령으로 Redis Pub-Sub 발행
3. 각 클라이언트가 레지스트리(`GitHubToken`)에 저장
4. 이후 자동 업데이트 시 Private 저장소 다운로드에 사용

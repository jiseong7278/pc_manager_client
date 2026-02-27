# PC Inspect Client

PC 점검 클라이언트 - Windows 서비스로 상시 실행

---

## 디렉토리 구조

```
pc_manager_client/
├── main.py          # 진입점
├── service.py       # Windows 서비스
├── collector.py     # PC 스펙 / 보안 프로그램 수집
├── redis_client.py  # Redis Pub-Sub 수신 + Stream 전송
├── updater.py       # GitHub Releases 자동 업데이트
├── config.py        # 설정
├── requirements.txt
├── build.spec       # PyInstaller 빌드 설정
└── logs/            # 런타임 생성
```

---

## 설정

`config.py` 에서 아래 항목을 서버 환경에 맞게 수정하세요.

```python
REDIS_HOST  = "192.168.0.1"          # 서버 IP
GITHUB_REPO = "YOUR_USERNAME/pc_manager_server"
```

---

## 개발 환경 실행 (테스트)

```powershell
pip install -r requirements.txt

# 서비스 없이 직접 실행
python main.py debug
```

---

## exe 빌드

```powershell
pip install pyinstaller
pyinstaller build.spec

# 빌드 결과: dist/PCInspectClient.exe
```

---

## Windows 서비스 설치 및 실행

관리자 권한 PowerShell에서 실행하세요.

```powershell
# 서비스 설치
.\PCInspectClient.exe install

# 서비스 시작
.\PCInspectClient.exe start

# 서비스 상태 확인
Get-Service PCInspectClient

# 서비스 중지
.\PCInspectClient.exe stop

# 서비스 제거
.\PCInspectClient.exe remove
```

---

## 자동 업데이트 동작 방식

1. 1시간마다 GitHub Releases API 조회
2. 현재 버전보다 높은 버전 발견 시 새 exe 다운로드
3. 배치 스크립트로 서비스 중지 → exe 교체 → 서비스 재시작
4. 업데이트 완료

GitHub Releases에 `PCInspectClient.exe` 파일이 포함된 릴리즈를 올리면 자동으로 배포됩니다.

---

## 데이터 흐름

```
서버 → Redis Pub-Sub (pc_inspect 채널) → 클라이언트 수신
클라이언트 → PC 스펙/보안 프로그램 수집
클라이언트 → Redis Stream (pc_reports) → 서버 수신
```

## 수집 데이터 항목

| 항목 | 내용 |
|------|------|
| MAC 주소 | 물리적 NIC 기준 |
| 컴퓨터 이름 | hostname |
| OS | 이름, 버전, 빌드, 아키텍처 |
| CPU | 이름, 코어 수, 최대 클럭 |
| GPU | 이름, VRAM, 드라이버 버전 |
| RAM | 전체/사용 가능 용량 |
| 디스크 | SSD/HDD 구분, 용량, 상태 |
| 보안 프로그램 | Windows Defender / V3 / 알약 버전 및 활성화 여부 |

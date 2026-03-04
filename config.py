# config.py
# 클라이언트 설정 - 환경에 맞게 수정하세요

# ── 버전 ──────────────────────────────────────────────────────────
CLIENT_VERSION = "1.0.0"

# ── Redis 서버 설정 ───────────────────────────────────────────────
REDIS_HOST    = "it-team"   # 서버 호스트명 또는 IP
REDIS_PORT    = 6379
REDIS_CHANNEL = "pc_inspect"    # subscribe 채널
STREAM_KEY    = "pc_reports"    # stream publish 키
STREAM_GROUP  = "report_group"

# ── GitHub 자동 업데이트 설정 ─────────────────────────────────────
GITHUB_REPO       = "jiseong7278/pc_manager_client"  # GitHub 저장소
GITHUB_API_URL    = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_TOKEN      = ""         # GitHub Personal Access Token (빌드 시 자동 주입)
UPDATE_CHECK_INTERVAL = 300    # 업데이트 확인 주기 (초, 기본 5분)

# ── 서비스 설정 ───────────────────────────────────────────────────
SERVICE_NAME    = "PCInspectClient"
SERVICE_DISPLAY = f"PC Inspect Client v{CLIENT_VERSION}"
SERVICE_DESC    = "PC 점검 클라이언트 서비스"

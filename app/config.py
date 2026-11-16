"""설정값. 프로젝트 최상위의 .env를 읽고, 값이 없으면 기본값을 쓴다."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

HOST = os.getenv("HOST") or "127.0.0.1"
PORT = int(os.getenv("PORT") or 8000)
DEBUG = (os.getenv("DEBUG") or "false").lower() == "true"
DB_PATH = Path(os.getenv("DB_PATH") or BASE_DIR / "data" / "gamepick.db")
STATIC_DIR = BASE_DIR / "static"
SURVEY_PATH = BASE_DIR / "data" / "survey.json"  # 설문 문항 (4.12 확정 후 작성)
# 배포(HTTPS) 때 true로. 로컬 http에서는 false여야 쿠키가 저장된다.
COOKIE_SECURE = (os.getenv("COOKIE_SECURE") or "false").lower() == "true"
# 서버 안에서 스토어 자동 수집을 하루 1번 (C7-a2). 끄려면 false
STORE_SYNC = (os.getenv("STORE_SYNC") or "true").lower() == "true"

# ---------- 배포 (C10, docs/C10_배포_지시.md) ----------
# DB·아이콘을 Replit Object Storage에 자동 백업·복원. 배포(Publishing) Secrets에만 true (작업 화면에는 넣지 않음)
BACKUP = (os.getenv("BACKUP") or "false").lower() == "true"
SEED_DB_PATH = BASE_DIR / "data" / "seed.db"  # DB 파일이 없고 복원할 백업도 없을 때 시작용 (scripts/make_seed_db.py)
# 서버가 켜질 때 아이콘 파일이 없는 게임은 스토어에서 다시 받기
ICON_FETCH = (os.getenv("ICON_FETCH") or "true").lower() == "true"
# 프록시 칸 수: X-Forwarded-For의 오른쪽에서 몇 번째를 실제 사용자 IP로 볼지. 0 = 헤더를 안 믿고 접속 주소 그대로 (로컬)
PROXY_HOPS = max(0, int(os.getenv("PROXY_HOPS") or 0))
# 쿠키 없는 요청으로 새 사용자를 만들 수 있는 횟수 (IP당 1시간)
USER_CREATE_PER_HOUR = max(1, int(os.getenv("USER_CREATE_PER_HOUR") or 60))
# 쓰기 요청(POST·PUT·PATCH·DELETE)의 Origin 헤더로 허용하는 주소 (쉼표 구분). 비우면 로컬 주소만
ALLOWED_ORIGINS = frozenset(
    o.strip().rstrip("/") for o in (os.getenv("ALLOWED_ORIGINS") or "").split(",") if o.strip()
) or frozenset({"http://127.0.0.1:8000", "http://localhost:8000", f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"})

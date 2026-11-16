"""
DB·아이콘 자동 백업·복원 (PROJECT.md 4.11 C10, docs/C10_배포_지시.md 1단계 2)·4)).

Replit 배포는 다시 배포할 때마다 파일이 초기화된다 → SQLite 파일과 아이콘을 Replit Object Storage에 둔다.
.env/Secrets의 BACKUP=true일 때만 동작 (배포 화면의 Secrets에만 넣음. 작업 화면에서 켜면 작업 화면 DB가 배포 백업을 덮어씀).

저장 이름 (Object Storage)
- gamepick-latest.db              10분마다 (바뀌었을 때만) + 서버가 꺼질 때
- gamepick-YYYYMMDD.db            하루 1번 (UTC 날짜), 14일 지난 것은 삭제
- gamepick-before-restore-….db    scripts/backup_db.py --restore 직전의 latest
- gamepick-restore-pending        --restore 표시. 있으면 그 latest로 복원해서 켜진 서버만 latest를 올리고(첫 백업 때 표시를 지움),
                                  그 전부터 켜져 있던 서버는 latest를 덮어쓰지 않음
- icons/{game_id}.png             게임 아이콘 (스토어에서 받은 것을 묶어 둠)

서버가 켜질 때: DB 파일이 없으면 latest 복원 → 없으면 data/seed.db. 이미 있으면 건드리지 않음. 그다음 표 구조를 최신으로.
아이콘은 별도 스레드: Object Storage에서 먼저 받고 → 그래도 없는 것만 스토어에서 (app/icons.py) → 새로 받은 것은 올림.
백업 실패는 로그만 남기고 서버는 계속. 상태(연속 실패 수·마지막 성공 시각)는 /api/health의 backup.
"""
import asyncio
import hashlib
import logging
import re
import shutil
import sqlite3
import tempfile
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app import config

LATEST = "gamepick-latest.db"
DAILY_PREFIX = "gamepick-"
DAILY_PATTERN = re.compile(r"gamepick-(\d{8})\.db")
RESTORE_MARKER = "gamepick-restore-pending"
ICON_PREFIX = "icons/"
BACKUP_EVERY = 10 * 60   # 초
KEEP_DAYS = 14

log = logging.getLogger("uvicorn.error")


class BackupUnavailable(RuntimeError):
    """BACKUP=true인데 Object Storage 라이브러리를 못 가져옴 → 서버 시작 실패 (11/16 채팅 결정)"""


# ---------- Object Storage ----------
class ReplitStorage:
    """replit-object-storage(Replit 안에서만 동작, Python 3.12) 감싸기. 테스트는 같은 메서드를 가진 가짜로 바꾼다."""

    def __init__(self):
        try:
            from replit.object_storage import Client
        except ImportError as error:
            raise BackupUnavailable(
                "BACKUP=true인데 replit-object-storage를 가져오지 못했어요 (Python 3.12에서 "
                "pip install -r requirements.txt). 백업 없이 켜면 다시 배포할 때 데이터가 사라지므로 서버를 켜지 않습니다."
            ) from error
        self.client = Client()  # 앱·배포의 기본 버킷

    def upload(self, name: str, path: Path) -> None:
        self.client.upload_from_filename(name, str(path))

    def download(self, name: str, path: Path) -> bool:
        """없으면 False"""
        if not self.client.exists(name):
            return False
        self.client.download_to_filename(name, str(path))
        return True

    def names(self, prefix: str = "") -> list[str]:
        return [obj.name for obj in self.client.list(prefix=prefix)]

    def exists(self, name: str) -> bool:
        return self.client.exists(name)

    def copy(self, src: str, dest: str) -> None:
        self.client.copy(src, dest)

    def delete(self, name: str) -> None:
        self.client.delete(name, ignore_not_found=True)

    def put_text(self, name: str, text: str) -> None:
        self.client.upload_from_text(name, text)


# ---------- 상태 (/api/health) ----------
class Status:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.enabled = False
        self.failures = 0
        self.last_success: str | None = None
        self.last_hash: str | None = None   # 마지막으로 올린 DB 사본의 해시 (바뀐 게 없으면 안 올림)
        self.last_daily: str | None = None  # 마지막 일별 백업 날짜 YYYYMMDD

    def public(self) -> dict:
        """숫자·시각만 (경로·키 없이)"""
        return {"enabled": self.enabled, "consecutive_failures": self.failures, "last_success": self.last_success}


status = Status()


# ---------- DB 사본 ----------
def snapshot(db_path: Path, out: Path) -> str:
    """SQLite Connection.backup()으로 일관된 사본을 out에 만들고 SHA-256을 돌려준다 (파일을 그대로 복사하지 않음)"""
    source, target = sqlite3.connect(db_path), sqlite3.connect(out)
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()
    return hashlib.sha256(out.read_bytes()).hexdigest()


def check_db(path: Path) -> None:
    """받은 파일이 정상 SQLite DB인지 (games 표가 있고 무결성 ok)"""
    conn = sqlite3.connect(path)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("무결성 검사 실패")
        conn.execute("SELECT COUNT(*) FROM games").fetchone()
    finally:
        conn.close()


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def prune_daily(storage, today: date, keep_days: int = KEEP_DAYS) -> list[str]:
    """일별 백업 중 keep_days일 지난 것 삭제. 지운 이름 목록"""
    cutoff = today - timedelta(days=keep_days)
    removed = []
    for name in storage.names(DAILY_PREFIX):
        match = DAILY_PATTERN.fullmatch(name)
        if match and datetime.strptime(match[1], "%Y%m%d").date() < cutoff:
            storage.delete(name)
            removed.append(name)
    return removed


def backup_once(storage, db_path: Path | None = None, today: date | None = None, final: bool = False,
                restored_from_marker: bool = False) -> dict:
    """한 번 백업. 바뀐 게 없으면 latest는 안 올림, 일별은 그날 처음이면 올림. 실패는 status에 세고 다시 던짐.
    restored_from_marker = 이 서버가 복원 대기 표시가 있는 latest로 시작함 → 올려도 되고, 올린 뒤 표시를 지움"""
    db_path = Path(db_path or config.DB_PATH)
    today = today or today_utc()
    day = today.strftime("%Y%m%d")
    result = {"latest": False, "daily": False, "pruned": [], "skipped_for_restore": False}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "snapshot.db"
            digest = snapshot(db_path, copy)
            if restored_from_marker and storage.exists(RESTORE_MARKER):
                storage.upload(LATEST, copy)  # 복원한 DB에서 이어진 내용 → 덮어써도 됨
                status.last_hash = digest
                storage.delete(RESTORE_MARKER)
                result["latest"] = True
            elif digest != status.last_hash:
                if storage.exists(RESTORE_MARKER):
                    # --restore가 latest를 바꿔 둔 상태 → 덮어쓰지 않음 (다시 배포하면 새 서버가 복원)
                    result["skipped_for_restore"] = True
                    log.warning("복원 대기 표시가 있어 latest 백업을 건너뜀")
                else:
                    storage.upload(LATEST, copy)
                    status.last_hash = digest
                    result["latest"] = True
            if status.last_daily != day and not final:
                storage.upload(f"{DAILY_PREFIX}{day}.db", copy)
                status.last_daily = day
                result["daily"] = True
                result["pruned"] = prune_daily(storage, today)
        with status.lock:
            status.failures = 0
            status.last_success = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return result
    except Exception:
        with status.lock:
            status.failures += 1
        raise


# ---------- 서버가 켜질 때 ----------
def restore_if_missing(storage, db_path: Path | None = None, seed_path: Path | None = None) -> str:
    """DB 파일이 없을 때만: latest → 없으면 seed. 돌려주는 값: 'kept' / 'latest' / 'latest-marked'(복원 대기 표시가
    있는 latest) / 'seed' / 'none'. 표시는 여기서 지우지 않음 — 이전 서버가 아직 꺼지는 중일 수 있어서 첫 백업 때 지움"""
    db_path = Path(db_path or config.DB_PATH)
    seed_path = Path(seed_path or config.SEED_DB_PATH)
    if db_path.exists():
        return "kept"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if storage is not None:
        # Object Storage 오류는 그대로 올려서 서버 시작을 멈춤 — 잠깐의 오류로 seed로 시작하면
        # 10분 뒤 빈 DB가 latest를 덮어쓰기 때문
        part = db_path.with_suffix(".download")
        if storage.download(LATEST, part):
            check_db(part)
            part.replace(db_path)
            return "latest-marked" if storage.exists(RESTORE_MARKER) else "latest"
        part.unlink(missing_ok=True)
        daily = [n for n in storage.names(DAILY_PREFIX) if DAILY_PATTERN.fullmatch(n)]
        if daily:
            raise RuntimeError(f"latest 백업은 없는데 일별 백업이 {len(daily)}개 있어요. 빈 DB로 시작하지 않습니다 → "
                               "python scripts/backup_db.py --restore 날짜 로 latest를 정한 뒤 다시 배포하세요.")
    if seed_path.exists():
        shutil.copyfile(seed_path, db_path)
        return "seed"
    return "none"


def ensure_schema(db_path: Path | None = None) -> None:
    """복원한 DB가 예전 코드의 것이어도 새 표·칸을 붙인다 (scripts/init_db.py와 같은 규칙, games.json은 다시 안 넣음)"""
    from app.db import get_connection
    from scripts import init_db

    with get_connection(db_path or config.DB_PATH) as conn:
        conn.executescript(init_db.SCHEMA)
        init_db.add_missing_columns(conn)
        init_db.migrate_games_tier(conn)


# ---------- 아이콘 ----------
def icon_dir() -> Path:
    return config.STATIC_DIR / "img" / "games"


def restore_icons(storage) -> int:
    """Object Storage에 있고 로컬에 없는 아이콘 받기. 받은 개수"""
    count = 0
    for name in storage.names(ICON_PREFIX):
        filename = name[len(ICON_PREFIX):]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}\.png", filename):
            continue
        path = icon_dir() / filename
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        if storage.download(name, part):
            part.replace(path)
            count += 1
    return count


def upload_icons(storage, already: set[str]) -> int:
    """로컬에 있는데 Object Storage에 없는 아이콘 올리기 (already = 올라가 있는 이름, 올린 것을 더함). 올린 개수"""
    count = 0
    folder = icon_dir()
    if not folder.exists():
        return 0
    for path in sorted(folder.glob("*.png")):
        name = ICON_PREFIX + path.name
        if name not in already:
            storage.upload(name, path)
            already.add(name)
            count += 1
    return count


# ---------- 서버 안 반복 ----------
class Runner:
    """lifespan에서 쓰는 묶음: 시작(복원) → 10분마다 백업 → 꺼질 때 마지막 백업"""

    def __init__(self, storage):
        self.storage = storage
        self.icons_up: set[str] = set()
        self.task: asyncio.Task | None = None
        self.restored_from_marker = False

    def start(self) -> str:
        status.enabled = True
        restored = restore_if_missing(self.storage)
        self.restored_from_marker = restored == "latest-marked"
        log.info("DB 복원: %s", restored)
        threading.Thread(target=self.icon_work, name="icon-restore", daemon=True).start()
        return restored

    def icon_work(self):
        """Object Storage → 그래도 없는 것은 스토어 → 새로 받은 것 올리기"""
        from app import icons

        try:
            self.icons_up = set(self.storage.names(ICON_PREFIX))
            log.info("아이콘 복원: %d개", restore_icons(self.storage))
            if config.ICON_FETCH:
                log.info("아이콘 스토어에서 받기: %s", icons.refill())
            log.info("아이콘 올리기: %d개", upload_icons(self.storage, self.icons_up))
        except Exception:  # noqa: BLE001 - 아이콘 실패로 서버를 멈추지 않음 (화면은 대체 표시)
            log.exception("아이콘 복원·올리기 실패")

    def tick(self, final: bool = False):
        try:
            result = backup_once(self.storage, final=final, restored_from_marker=self.restored_from_marker)
            if result["latest"]:
                self.restored_from_marker = False
            if any((result["latest"], result["daily"])):
                log.info("백업: %s", result)
            if not final:
                upload_icons(self.storage, self.icons_up)  # 스토어 수집으로 새로 생긴 아이콘
        except Exception:  # noqa: BLE001 - 백업 실패로 서버를 멈추지 않음
            log.exception("백업 실패 (연속 %d번)", status.failures)

    async def loop(self):
        while True:
            await asyncio.sleep(BACKUP_EVERY)
            await asyncio.to_thread(self.tick)

    def launch(self):
        self.task = asyncio.create_task(self.loop())

    async def stop(self):
        if self.task:
            self.task.cancel()
        await asyncio.to_thread(self.tick, True)

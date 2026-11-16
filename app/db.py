"""SQLite 연결. 테이블 생성은 scripts/init_db.py.

연결은 항상 쓰고 나면 닫는다.
- 스크립트: `with get_connection() as conn:`
- API: `conn = Depends(get_db)` (요청마다 열고 닫음)
"""
import sqlite3
from contextlib import contextmanager

from app.config import DB_PATH


def _connect(path) -> sqlite3.Connection:
    # FastAPI는 의존성과 엔드포인트를 다른 스레드에서 돌릴 수 있다. 연결은 요청 하나에서만 쓰므로 공유 걱정 없음.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_connection(path=None):
    """성공하면 commit, 오류면 rollback, 어느 쪽이든 close. path를 주면 다른 DB 파일(테스트용)."""
    conn = _connect(path or DB_PATH)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_db():
    """FastAPI 의존성."""
    with get_connection() as conn:
        yield conn

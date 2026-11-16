"""라우터가 같이 쓰는 의존성: 임시 사용자 (4.3, 4.11 C4)."""
import logging
import random
import sqlite3
import uuid

from fastapi import Depends, Request, Response

from app import config
from app.db import get_db
from app.security import check_user_create, forwarded_hops

log = logging.getLogger("uvicorn.error")

GAME_ID_PATTERN = r"^[A-Za-z0-9_-]{1,40}$"
FEATURE_PATTERN = r"^(genre|subgenre|view|play|mood|bm):[^:,]{1,30}$"  # '종류:태그' (예: bm:가챠)

COOKIE_NAME = "gp_uid"
COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # 1년
EXP_GROUPS = ("random", "survey", "feedback")


def _is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def find_user(request: Request, conn: sqlite3.Connection) -> sqlite3.Row | None:
    """쿠키의 ID가 DB에 있는 사용자면 그 행, 아니면 None (위조·만료 값은 무시)."""
    uid = request.cookies.get(COOKIE_NAME)
    if not uid or not _is_uuid(uid):
        return None
    return conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def get_user(request: Request, response: Response, conn: sqlite3.Connection = Depends(get_db)) -> sqlite3.Row:
    """현재 사용자. 쿠키가 없거나 DB에 없는 ID면 새로 만들고 쿠키를 심는다 (실험 그룹은 무작위)."""
    user = find_user(request, conn)
    if user is None:
        check_user_create(request)  # IP당 1시간 제한 (C10)
        log.info("새 사용자 (X-Forwarded-For %d칸)", forwarded_hops(request))  # PROXY_HOPS 맞추기용, IP는 안 남김
        uid = str(uuid.uuid4())
        conn.execute("INSERT INTO users (id, exp_group) VALUES (?, ?)", (uid, random.choice(EXP_GROUPS)))
        user = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        response.set_cookie(COOKIE_NAME, uid, max_age=COOKIE_MAX_AGE, httponly=True,
                            samesite="lax", secure=config.COOKIE_SECURE)
    return user


def clear_user_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, httponly=True, samesite="lax", secure=config.COOKIE_SECURE)

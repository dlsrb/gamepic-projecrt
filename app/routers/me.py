"""마이페이지·홈: 내 정보, 닉네임, 취향 시각화, 최근 본 게임 (4.10)."""
import re
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, field_validator

from app import config
from app.db import get_db
from app.deps import get_user
from app.reco import repository as repo
from app.services import Catalog, card, profile_for, taste

router = APIRouter(prefix="/api/me", tags=["마이페이지"])

HISTORY_DEFAULT, HISTORY_MAX = 10, 20

NICKNAME_CHARS = re.compile(r"[가-힣A-Za-z0-9]+")
HANGUL, LATIN = re.compile(r"[가-힣]"), re.compile(r"[A-Za-z]")
NICKNAME_LENGTH = {"hangul": (2, 10), "latin": (4, 12), "mixed": (2, 12)}


def check_nickname(value: str) -> str:
    """한글 2~10자 / 영어 4~12자 / 한글+영어 혼합 2~12자. 한글·영어·숫자만 (숫자는 길이에 포함)."""
    value = value.strip()
    if not NICKNAME_CHARS.fullmatch(value):
        raise ValueError("닉네임은 한글·영어·숫자만 쓸 수 있습니다.")
    has_hangul, has_latin = bool(HANGUL.search(value)), bool(LATIN.search(value))
    if not (has_hangul or has_latin):
        raise ValueError("닉네임에는 한글이나 영어가 들어가야 합니다.")
    kind = "mixed" if has_hangul and has_latin else "hangul" if has_hangul else "latin"
    low, high = NICKNAME_LENGTH[kind]
    if not low <= len(value) <= high:
        label = {"hangul": "한글", "latin": "영어", "mixed": "한글·영어 혼합"}[kind]
        raise ValueError(f"{label} 닉네임은 {low}~{high}자입니다.")
    return value


class NicknameIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nickname: str

    @field_validator("nickname")
    @classmethod
    def _valid(cls, value: str) -> str:
        return check_nickname(value)


class GroupIn(BaseModel):
    group: Literal["random", "survey", "feedback"]


def _me(conn: sqlite3.Connection, user) -> dict:
    data = {"nickname": user["nickname"], "survey_done": bool(user["survey_done"]), "created_at": user["created_at"],
            "disliked_tags": repo.load_survey(conn, user["id"]).get("dislikes", [])}  # 설정의 비선호 관리 화면용
    if config.DEBUG:  # 실험 그룹은 화면에 드러내지 않는다. 개발 확인용으로만
        data["exp_group"] = user["exp_group"]
    return data


@router.get("")
def get_me(user=Depends(get_user), conn: sqlite3.Connection = Depends(get_db)):
    return _me(conn, user)


@router.patch("")
def change_nickname(body: NicknameIn, user=Depends(get_user), conn: sqlite3.Connection = Depends(get_db)):
    conn.execute("UPDATE users SET nickname = ? WHERE id = ?", (body.nickname, user["id"]))
    return _me(conn, conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone())


@router.get("/taste")
def my_taste(user=Depends(get_user), conn: sqlite3.Connection = Depends(get_db)):
    """레이더 차트용: 태그 종류 6개 축(점수가 있는 태그의 평균), 가장 좋아하는·싫어하는 태그"""
    return taste(conn, user["id"])


@router.get("/history")
def history(limit: int = Query(HISTORY_DEFAULT, ge=1, le=HISTORY_MAX),
            user=Depends(get_user), conn: sqlite3.Connection = Depends(get_db)):
    """최근 본 게임 (상세 조회 기록, 최신순, 중복 없이)"""
    rows = conn.execute(
        """
        SELECT game_id, MAX(id) AS last_id, MAX(created_at) AS viewed_at FROM reactions
        WHERE user_id = ? AND type = 'view' GROUP BY game_id ORDER BY last_id DESC LIMIT ?
        """,
        (user["id"], limit),
    ).fetchall()
    catalog = Catalog(conn)
    profile = profile_for(conn, user, catalog)
    return {"games": [card(catalog, catalog.get(row["game_id"]), profile, viewed_at=row["viewed_at"])
                      for row in rows if catalog.get(row["game_id"])]}


@router.put("/exp-group")
def set_group(body: GroupIn, user=Depends(get_user), conn: sqlite3.Connection = Depends(get_db)):
    """개발 확인용: DEBUG=true일 때만 실험 그룹을 바꾼다. 아니면 없는 주소처럼 404."""
    if not config.DEBUG:
        raise HTTPException(404, "Not Found")
    conn.execute("UPDATE users SET exp_group = ? WHERE id = ?", (body.group, user["id"]))
    return {"exp_group": body.group}

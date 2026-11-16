"""상세 페이지 자유 댓글 (C8-c) + 댓글 추천·비추천 (C9) + 정렬·cursor (C9-b). 리뷰·체크 항목(C7-b)은 보류, 신고·필터·대댓글은 나중에."""
import re
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictInt, field_validator

from app.db import get_db
from app.deps import get_user
from app.routers.games import GameIdPath
from app.security import comment_rate_limit, comment_vote_rate_limit

router = APIRouter(prefix="/api", tags=["댓글"])

BODY_MAX, LINES_MAX = 300, 10
LIST_DEFAULT, LIST_MAX = 20, 50
ANONYMOUS = "익명 사용자"
# 줄바꿈(\n)만 남기고 제어 문자 + 글자 방향을 뒤집는 보이지 않는 문자는 지운다
CONTROL_CHARS = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩﻿]")


def clean_body(value: str) -> str:
    """줄바꿈 통일(\r\n → \n) → 제어 문자 제거 → 앞뒤 공백 제거 → 1~300자·최대 10줄 검사"""
    text = CONTROL_CHARS.sub("", value.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if not text:
        raise ValueError("댓글 내용을 입력해 주세요.")
    if len(text) > BODY_MAX:
        raise ValueError(f"댓글은 {BODY_MAX}자까지 쓸 수 있어요. (지금 {len(text)}자)")
    if text.count("\n") + 1 > LINES_MAX:
        raise ValueError(f"댓글은 {LINES_MAX}줄까지 쓸 수 있어요.")
    return text


class CommentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str

    @field_validator("body")
    @classmethod
    def _valid(cls, value: str) -> str:
        if len(value) > BODY_MAX * 4:  # 정리하기 전에도 너무 긴 입력은 바로 거절
            raise ValueError(f"댓글은 {BODY_MAX}자까지 쓸 수 있어요.")
        return clean_body(value)


def _game_exists(conn: sqlite3.Connection, game_id: str) -> None:
    if conn.execute("SELECT 1 FROM games WHERE id = ? AND review_status = 'ok'", (game_id,)).fetchone() is None:
        raise HTTPException(404, "게임을 찾을 수 없습니다.")


NO_VOTES = {"likes": 0, "dislikes": 0, "my_vote": 0}


def _votes(conn: sqlite3.Connection, comment_ids: list[int], user_id: str) -> dict[int, dict]:
    """댓글 여러 개의 추천·비추천 수 + 내 상태를 쿼리 한 번으로 (C9). 누가 눌렀는지는 내보내지 않는다"""
    if not comment_ids:
        return {}
    marks = ", ".join("?" * len(comment_ids))
    rows = conn.execute(
        "SELECT comment_id, SUM(value = 1) AS likes, SUM(value = -1) AS dislikes, "
        "COALESCE(MAX(CASE WHEN user_id = ? THEN value END), 0) AS my_vote "
        f"FROM comment_votes WHERE comment_id IN ({marks}) GROUP BY comment_id",
        (user_id, *comment_ids)).fetchall()
    return {r["comment_id"]: {"likes": r["likes"], "dislikes": r["dislikes"], "my_vote": r["my_vote"]} for r in rows}


def _row_out(row: sqlite3.Row, user_id: str, votes: dict | None = None) -> dict:
    return {"id": row["id"], "nickname": row["nickname"] or ANONYMOUS, "body": row["body"],
            "created_at": row["created_at"], "edited": row["updated_at"] is not None, "mine": row["user_id"] == user_id,
            **(votes or NO_VOTES)}


def _one_out(conn: sqlite3.Connection, comment_id: int, user_id: str) -> dict:
    row = conn.execute(f"{SELECT} WHERE c.id = ?", (comment_id,)).fetchone()
    return _row_out(row, user_id, _votes(conn, [comment_id], user_id).get(comment_id))


SELECT = ("SELECT c.id, c.user_id, c.body, c.created_at, c.updated_at, u.nickname "
          "FROM comments c JOIN users u ON u.id = c.user_id")


def _own_comment(conn: sqlite3.Connection, comment_id: int, user_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT id, user_id FROM comments WHERE id = ?", (comment_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "댓글을 찾을 수 없습니다.")
    if row["user_id"] != user_id:
        raise HTTPException(403, "내가 쓴 댓글만 고치거나 지울 수 있어요.")
    return row


# ---------- 목록 정렬 + 다음 쪽 표시(cursor) (C9-b) ----------
# 최신순 = id 큰 것 먼저 / 등록순 = id 작은 것 먼저 / 추천순 = 👍 많은 순, 같으면 최신순(id 큰 것 먼저)
# cursor는 마지막으로 받은 댓글 위치: "latest.<id>" / "oldest.<id>" / "top.<👍 수>.<id>". 화면은 그대로 돌려보내기만
CommentSort = Literal["latest", "oldest", "top"]
CURSOR = {"latest": re.compile(r"latest\.([1-9]\d{0,17})"), "oldest": re.compile(r"oldest\.([1-9]\d{0,17})"),
          "top": re.compile(r"top\.(0|[1-9]\d{0,17})\.([1-9]\d{0,17})")}
LIKES = "COALESCE(v.likes, 0)"
LIST_SELECT = (
    f"SELECT c.id, c.user_id, c.body, c.created_at, c.updated_at, u.nickname, {LIKES} AS likes "
    "FROM comments c JOIN users u ON u.id = c.user_id "
    "LEFT JOIN (SELECT comment_id, SUM(value = 1) AS likes FROM comment_votes "
    "           WHERE comment_id IN (SELECT id FROM comments WHERE game_id = ?) GROUP BY comment_id) v "
    "ON v.comment_id = c.id WHERE c.game_id = ?")
ORDER = {"latest": "c.id DESC", "oldest": "c.id ASC", "top": f"{LIKES} DESC, c.id DESC"}


def _after(sort: str, cursor: str) -> tuple[str, tuple]:
    """cursor → 그다음 댓글만 고르는 SQL 조건. 형식이 틀리거나 정렬 종류가 다르면 422"""
    match = CURSOR[sort].fullmatch(cursor)
    if match is None:
        raise HTTPException(422, "목록 위치(cursor)가 올바르지 않아요. 처음부터 다시 불러와 주세요.")
    if sort == "latest":
        return "c.id < ?", (int(match[1]),)
    if sort == "oldest":
        return "c.id > ?", (int(match[1]),)
    likes, last_id = int(match[1]), int(match[2])
    return f"({LIKES} < ? OR ({LIKES} = ? AND c.id < ?))", (likes, likes, last_id)


def _cursor(sort: str, row: sqlite3.Row) -> str:
    return f"top.{row['likes']}.{row['id']}" if sort == "top" else f"{sort}.{row['id']}"


@router.get("/games/{game_id}/comments")
def list_comments(game_id: GameIdPath, sort: CommentSort = "latest",
                  cursor: str | None = Query(None, max_length=64),
                  before_id: int | None = Query(None, ge=1),
                  limit: int = Query(LIST_DEFAULT, ge=1, le=LIST_MAX),
                  user=Depends(get_user), conn: sqlite3.Connection = Depends(get_db)):
    """sort = latest(기본, 예전 동작) / oldest / top. 다음 쪽은 응답의 next_cursor를 cursor로.
    before_id(C8-c)는 latest에서만 계속 받음 (= cursor "latest.<id>")"""
    if before_id is not None:
        if sort != "latest" or cursor is not None:
            raise HTTPException(422, "before_id는 최신순(sort=latest)에서 cursor 없이만 쓸 수 있어요.")
        cursor = f"latest.{before_id}"
    _game_exists(conn, game_id)
    total = conn.execute("SELECT COUNT(*) FROM comments WHERE game_id = ?", (game_id,)).fetchone()[0]
    where, params = _after(sort, cursor) if cursor is not None else ("1", ())
    rows = conn.execute(f"{LIST_SELECT} AND {where} ORDER BY {ORDER[sort]} LIMIT ?",
                        (game_id, game_id, *params, limit + 1)).fetchall()
    rows, has_more = rows[:limit], len(rows) > limit
    votes = _votes(conn, [r["id"] for r in rows], user["id"])
    return {"total": total, "sort": sort,
            "comments": [_row_out(r, user["id"], votes.get(r["id"])) for r in rows],
            "has_more": has_more, "next_cursor": _cursor(sort, rows[-1]) if has_more else None}


@router.post("/games/{game_id}/comments", status_code=201, dependencies=[Depends(comment_rate_limit)])
def create_comment(game_id: GameIdPath, body: CommentIn, user=Depends(get_user),
                   conn: sqlite3.Connection = Depends(get_db)):
    _game_exists(conn, game_id)
    comment_id = conn.execute("INSERT INTO comments (game_id, user_id, body) VALUES (?, ?, ?)",
                              (game_id, user["id"], body.body)).lastrowid
    return _one_out(conn, comment_id, user["id"])


@router.patch("/comments/{comment_id}", dependencies=[Depends(comment_rate_limit)])
def edit_comment(comment_id: int, body: CommentIn, user=Depends(get_user), conn: sqlite3.Connection = Depends(get_db)):
    _own_comment(conn, comment_id, user["id"])
    conn.execute("UPDATE comments SET body = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (body.body, comment_id))
    return _one_out(conn, comment_id, user["id"])


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: int, user=Depends(get_user), conn: sqlite3.Connection = Depends(get_db)):
    """실제 삭제"""
    _own_comment(conn, comment_id, user["id"])
    conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))  # 추천·비추천은 ON DELETE CASCADE로 같이 삭제
    return JSONResponse({"deleted": True})


class VoteIn(BaseModel):
    """1 추천 / -1 비추천 / 0 취소. strict: "1"·true 같은 값은 422"""
    model_config = ConfigDict(extra="forbid")
    value: StrictInt  # Literal[1, -1, 0]은 True == 1이라 true를 통과시킴 → 정수만 받고 아래에서 범위 검사

    @field_validator("value")
    @classmethod
    def _one_of(cls, value: int) -> int:
        if value not in (1, -1, 0):
            raise ValueError("value는 1(추천), -1(비추천), 0(취소) 중 하나여야 해요.")
        return value


@router.put("/comments/{comment_id}/vote", dependencies=[Depends(comment_vote_rate_limit)])
def vote_comment(comment_id: int, body: VoteIn, user=Depends(get_user), conn: sqlite3.Connection = Depends(get_db)):
    """댓글 추천·비추천 (C9). 같은 값을 다시 보내도 결과는 같다. 게임 추천 엔진(태그 점수)에는 반영하지 않음"""
    row = conn.execute("SELECT user_id FROM comments WHERE id = ?", (comment_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "댓글을 찾을 수 없습니다.")
    if row["user_id"] == user["id"]:
        raise HTTPException(403, "내 댓글에는 누를 수 없어요.")
    if body.value == 0:
        conn.execute("DELETE FROM comment_votes WHERE comment_id = ? AND user_id = ?", (comment_id, user["id"]))
    else:
        conn.execute(
            "INSERT INTO comment_votes (comment_id, user_id, value) VALUES (?, ?, ?) "
            "ON CONFLICT (comment_id, user_id) DO UPDATE SET value = excluded.value, created_at = CURRENT_TIMESTAMP "
            "WHERE comment_votes.value != excluded.value",
            (comment_id, user["id"], body.value))
    return _votes(conn, [comment_id], user["id"]).get(comment_id, NO_VOTES)

"""설정 페이지: 비선호 태그 관리, 추천 기록 초기화, 내 데이터 내려받기·삭제 (4.10)."""
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.db import get_db
from app.deps import FEATURE_PATTERN, clear_user_cookie, find_user, get_user
from app.reco import repository as repo
from app.reco.recommender import category_of, label_of

router = APIRouter(prefix="/api/me", tags=["설정"])

Feature = Annotated[str, StringConstraints(pattern=FEATURE_PATTERN)]


class DislikedTagsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tags: list[Feature] = Field(max_length=30)  # '종류:태그' (예: bm:가챠)


@router.put("/disliked-tags")
def set_disliked(body: DislikedTagsIn, user=Depends(get_user), conn: sqlite3.Connection = Depends(get_db)):
    """설문의 비선호 응답을 바꾸고 점수 재계산. 이 태그를 가진 게임은 후보에서 제외된다."""
    tags = list(dict.fromkeys(body.tags))
    unknown = [f for f in tags if conn.execute("SELECT 1 FROM tags WHERE category = ? AND name = ?",
                                               (category_of(f), label_of(f))).fetchone() is None]
    if unknown:
        raise HTTPException(422, f"없는 태그: {', '.join(unknown)}")
    repo.set_disliked_tags(conn, user["id"], tags)
    return {"tags": tags}


@router.post("/reset")
def reset_history(user=Depends(get_user), conn: sqlite3.Connection = Depends(get_db)):
    """추천 기록 초기화: 따봉·스토어·관심 없음 기록 삭제 → 설문 점수로 재계산 (최근 본 게임은 유지)"""
    cur = conn.execute("DELETE FROM reactions WHERE user_id = ? AND type IN ('like', 'store', 'dislike')",
                       (user["id"],))
    repo.recompute_scores(conn, user["id"])
    return {"deleted_reactions": cur.rowcount}


@router.get("/export")
def export(response: Response, user=Depends(get_user), conn: sqlite3.Connection = Depends(get_db)):
    """내 데이터 내려받기 (JSON 파일)"""
    uid = user["id"]
    scores = conn.execute(
        "SELECT t.category, t.name, s.score FROM user_tag_scores s JOIN tags t ON t.id = s.tag_id "
        "WHERE s.user_id = ? ORDER BY t.id", (uid,))
    reactions = conn.execute(
        "SELECT game_id, type, created_at FROM reactions WHERE user_id = ? ORDER BY id", (uid,))
    comments = conn.execute(  # C8-c
        "SELECT game_id, body, created_at, updated_at FROM comments WHERE user_id = ? ORDER BY id", (uid,))
    comment_votes = conn.execute(  # C9: 내가 누른 추천(1)·비추천(-1)
        "SELECT comment_id, value, created_at FROM comment_votes WHERE user_id = ? ORDER BY created_at, comment_id",
        (uid,))
    response.headers["Content-Disposition"] = 'attachment; filename="gamepick-my-data.json"'
    return {
        "user": {"id": uid, "nickname": user["nickname"], "survey_done": bool(user["survey_done"]),
                 "created_at": user["created_at"]},
        "survey_answers": repo.load_answers(conn, uid),  # 설문 응답 원본
        "survey": repo.load_survey(conn, uid),           # 추천에 쓰는 형식으로 바꾼 것
        "tag_scores": [dict(r) for r in scores],
        "reactions": [dict(r) for r in reactions],
        "comments": [dict(r) for r in comments],
        "comment_votes": [dict(r) for r in comment_votes],
    }


@router.delete("")
def delete_me(request: Request, response: Response, conn: sqlite3.Connection = Depends(get_db)):
    """내 데이터 삭제: users 행 삭제(설문·점수·반응·댓글 연쇄 삭제) + 쿠키 제거. 검색 기록은 원래 사용자와 연결되지 않음."""
    user = find_user(request, conn)
    if user is not None:
        conn.execute("DELETE FROM users WHERE id = ?", (user["id"],))
    clear_user_cookie(response)
    return {"deleted": user is not None}

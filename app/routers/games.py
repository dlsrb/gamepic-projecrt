"""상세 페이지: 게임 정보, 비슷한 게임, 리뷰 분석, 반응 (4.10)."""
import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, StringConstraints

from app.db import get_db
from app.deps import GAME_ID_PATTERN, get_user
from app.reco import repository as repo
from app.reco.reco_config import SIMILAR_BLEND_RATIO
from app.reco.recommender import FEATURE_FIELDS, game_features, match_percent, reasons, similar_games
from app.security import rate_limit
from app.services import GAME_DETAIL_COLUMNS, Catalog, card, like_count, profile_for, rank_of

router = APIRouter(prefix="/api", tags=["상세"])

GameIdPath = Annotated[str, Path(pattern=GAME_ID_PATTERN)]
SIMILAR_DEFAULT, SIMILAR_MAX = 6, 12


def _game_or_404(catalog: Catalog, game_id: str) -> dict:
    game = catalog.get(game_id)
    if game is None:
        raise HTTPException(404, "게임을 찾을 수 없습니다.")
    return game


def _liked(conn: sqlite3.Connection, user_id: str, game_id: str) -> bool:
    return conn.execute("SELECT 1 FROM reactions WHERE user_id = ? AND game_id = ? AND type = 'like'",
                        (user_id, game_id)).fetchone() is not None


@router.get("/games/{game_id}")
def game_detail(game_id: GameIdPath, user=Depends(get_user), conn: sqlite3.Connection = Depends(get_db)):
    """상세 정보 + 부합도 + 추천 수 + 순위. 조회할 때마다 view 기록 (최근 본 게임)."""
    catalog = Catalog(conn)
    game = _game_or_404(catalog, game_id)
    repo.record_reaction(conn, user["id"], game_id, "view")
    profile = profile_for(conn, user, catalog)
    row = catalog.rows[game_id]
    return {
        "id": game_id,
        "name": game["name"],
        # 미검수 자동 수집 게임은 장르만 보임 (C12-a)
        "tags": {c: ([game[c]] if c == "genre" else game[c] if catalog.checked(game) else []) for c in FEATURE_FIELDS},
        "tags_checked": catalog.checked(game),
        **{c: row[c] for c in GAME_DETAIL_COLUMNS},
        "indie": game["indie"],
        "badges": catalog.badges.of(game_id),  # C12-e (화면의 "인디" 칩은 이 배지로)
        "match": match_percent(game, profile),
        "reasons": reasons(game, profile),
        "like_count": like_count(conn, game_id),
        "liked": _liked(conn, user["id"], game_id),
        "rank": rank_of(conn, catalog, game_id),
    }


@router.get("/games/{game_id}/similar")
def similar(game_id: GameIdPath, limit: int = Query(SIMILAR_DEFAULT, ge=1, le=SIMILAR_MAX),
            user=Depends(get_user), conn: sqlite3.Connection = Depends(get_db)):
    """
    이 게임과 비슷하면서 내 취향인 게임 (C7-e). similarity = 이 게임 기준 유사도 %, match = 내 취향 부합도 %
    순서 = similarity × 0.6 + match × 0.4 (SIMILAR_BLEND_RATIO). 지금 게임·내 비선호 태그가 있는 게임은 뺀다.
    취향 프로필이 비었으면 유사도 순 그대로
    """
    catalog = Catalog(conn)
    _game_or_404(catalog, game_id)
    profile = profile_for(conn, user, catalog)
    items = [card(catalog, r["game"], profile, similarity=r["match"])
             for r in similar_games(game_id, catalog.games, k=len(catalog.games))
             if r["game"]["id"] != game_id and not set(game_features(r["game"])) & profile.excluded]
    if profile.weights:
        sim_ratio, match_ratio = SIMILAR_BLEND_RATIO
        items.sort(key=lambda c: -(c["similarity"] * sim_ratio + c["match"] * match_ratio))  # 같으면 유사도 순 유지
    return {"games": items[:limit]}


@router.get("/games/{game_id}/reviews")
def reviews(game_id: GameIdPath, conn: sqlite3.Connection = Depends(get_db)):
    """별점 분포 + 별점별 문장 감정 + 주제별 불만. 모델 결과(model)가 있으면 그것, 없으면 임시(temp)."""
    _game_or_404(Catalog(conn), game_id)
    sources = {row["source"] for row in conn.execute(
        "SELECT source FROM review_stats WHERE game_id = ? UNION SELECT source FROM review_topics WHERE game_id = ?",
        (game_id, game_id))}
    source = "model" if "model" in sources else "temp" if "temp" in sources else None
    stars = conn.execute(
        "SELECT star, review_count, pos, neg, neu, irr FROM review_stats WHERE game_id = ? AND source = ? ORDER BY star",
        (game_id, source)).fetchall()
    topics = conn.execute(
        "SELECT topic, neg_count, neg_ratio FROM review_topics WHERE game_id = ? AND source = ? ORDER BY neg_ratio DESC",
        (game_id, source)).fetchall()
    return {"ready": source is not None, "source": source,
            "stars": [dict(r) for r in stars], "topics": [dict(r) for r in topics]}


# ---------- 반응 ----------
class ReactionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    game_id: Annotated[str, StringConstraints(pattern=GAME_ID_PATTERN)]
    type: Literal["like", "store", "dislike"]


@router.post("/reactions", dependencies=[Depends(rate_limit("reaction"))])
def react(body: ReactionIn, user=Depends(get_user), conn: sqlite3.Connection = Depends(get_db)):
    """따봉(게임당 1회) / 스토어 이동(점수는 하루 1회) / 관심 없음 → 태그 점수 갱신"""
    _game_or_404(Catalog(conn), body.game_id)
    applied = repo.record_reaction(conn, user["id"], body.game_id, body.type)
    return {"applied": applied, "like_count": like_count(conn, body.game_id),
            "liked": _liked(conn, user["id"], body.game_id)}


@router.delete("/reactions/like/{game_id}", dependencies=[Depends(rate_limit("reaction"))])
def unlike(game_id: GameIdPath, user=Depends(get_user), conn: sqlite3.Connection = Depends(get_db)):
    """따봉 취소 → 점수 재계산"""
    _game_or_404(Catalog(conn), game_id)
    cancelled = repo.cancel_like(conn, user["id"], game_id)
    return {"cancelled": cancelled, "like_count": like_count(conn, game_id), "liked": False}

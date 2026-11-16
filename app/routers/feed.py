"""홈: 맞춤·신작·인기 카드, 추천 순위 (4.10)."""
import re
import sqlite3
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.db import get_db
from app.deps import get_user
from app.services import Catalog, card, match_feed, profile_for, ranking

router = APIRouter(prefix="/api", tags=["홈"])

FEED_DEFAULT, FEED_MAX = 10, 30
NEW_DAYS = 90  # 신작 = 출시 90일 이내
RANKING_SIZE = 10


@router.get("/feed")
def feed(section: Literal["match", "new", "popular"] = "match",
         limit: int = Query(FEED_DEFAULT, ge=1, le=FEED_MAX),
         min_match: int = Query(0, ge=0, le=100),
         offset: int = Query(0, ge=0, le=10_000),
         user=Depends(get_user), conn: sqlite3.Connection = Depends(get_db)):
    """
    match: 추천 엔진 결과 (탐색 20% 포함, 실험 그룹에 따라 계산 방식이 다름)
           min_match: 취향 % 이 값 이상 + 태그 4개 이상만 (탐색 슬롯 제외). 기준 통과가 3개 미만이면 기준 없이 주고 fallback=true
    popular: 스토어 인기 순위 (Google Play·App Store 무료 인기 게임 차트 중 더 높은 순위, 차트 밖 게임 제외) (C7-a2)
    new: 출시 NEW_DAYS일 이내 (출시 예정·출시일 없는 게임 제외) (C7-a2).
         순서 (C8-a): 이름에 한글이 있거나 별점 수 1개 이상인 게임 먼저 → 나머지, 각 묶음 안에서 최신순
    offset: new·popular에서 앞의 몇 개를 건너뛸지 ("더 보기", C8-b). match에는 쓰지 않음. 응답 has_more
    추천 순위(따봉 수)는 /api/ranking 그대로
    """
    catalog = Catalog(conn)
    if section == "match":
        games, fallback = match_feed(conn, user, catalog, limit, min_match)
        return {"section": section, "games": games, "fallback": fallback}

    profile = profile_for(conn, user, catalog)
    if section == "popular":
        ranked = []
        for g in catalog.games:
            row = catalog.rows[g["id"]]
            ranks = [r for r in (row["chart_rank_android"], row["chart_rank_ios"]) if r]
            if ranks:
                ranked.append((min(ranks), g))
        ranked.sort(key=lambda x: x[0])
        page = ranked[offset:offset + limit]
        items = [card(catalog, g, profile, chart_rank=rank) for rank, g in page]
        total = len(ranked)
    else:
        today = date.today()
        since = (today - timedelta(days=NEW_DAYS)).isoformat()
        dated = [g for g in catalog.games
                 if since <= (catalog.rows[g["id"]]["release_date"] or "") <= today.isoformat()]
        dated.sort(key=lambda g: catalog.rows[g["id"]]["release_date"], reverse=True)
        dated.sort(key=lambda g: not _familiar(g, catalog.rows[g["id"]]))  # 안정 정렬 → 묶음 안 최신순 유지
        items = [card(catalog, g, profile, release_date=catalog.rows[g["id"]]["release_date"])
                 for g in dated[offset:offset + limit]]
        total = len(dated)
    return {"section": section, "games": items, "has_more": offset + limit < total}


HANGUL = re.compile(r"[가-힣]")


def _familiar(game: dict, row) -> bool:
    """신작 탭 앞쪽 묶음: 이름에 한글이 있거나 별점 수가 1개 이상 (C8-a, 별점 0개 해외 소형 앱은 뒤로)"""
    return bool(HANGUL.search(game["name"])) or (row["store_rating_count"] or 0) >= 1


@router.get("/ranking")
def get_ranking(user=Depends(get_user), conn: sqlite3.Connection = Depends(get_db)):
    """추천(따봉) 수 1~10위"""
    catalog = Catalog(conn)
    profile = profile_for(conn, user, catalog)
    return {"ranking": [card(catalog, catalog.get(item["game_id"]), profile,
                             rank=item["rank"], like_count=item["like_count"])
                        for item in ranking(conn, catalog)[:RANKING_SIZE]]}

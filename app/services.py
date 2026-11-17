"""
여러 라우터가 같이 쓰는 조회·가공 (게임 카드, 추천 수·순위, 실험 그룹별 취향).
추천 계산 자체는 app/reco/, 여기서는 DB 결과를 화면용 모양으로 맞춘다.
"""
import json
import random
import re
import sqlite3

from app import config
from app.reco import repository as repo
from app.reco.reco_config import EXPLORE_RATIO, HOME_MIN_TAGS
from app.reco.recommender import (
    FEATURE_FIELDS, UserProfile, build_profile, game_features, label_of, match_percent, passes_filter, reasons,
    recommend, set_best_score,
)

GAME_DETAIL_COLUMNS = ("developer", "tier", "description", "thumbnail_url", "video_url", "android_url", "ios_url",
                       "release_date", "store_rating", "store_rating_count", "installs")


# ---------- 게임 ----------
class Catalog:
    """요청 하나 동안 쓰는 게임 목록 (태그 포함) + games 테이블의 나머지 컬럼."""

    def __init__(self, conn: sqlite3.Connection):
        self.games = repo.load_games(conn)
        self.by_id = {g["id"]: g for g in self.games}
        self.rows = {row["id"]: row for row in conn.execute("SELECT * FROM games")}

    def get(self, game_id: str) -> dict | None:
        return self.by_id.get(game_id)


def tag_names(game: dict) -> list[str]:
    """장르 다음으로 세부장르·시점·플레이·분위기·과금 순서의 태그 이름"""
    return [label_of(f) for f in game_features(game)]


def card(catalog: Catalog, game: dict, profile: UserProfile | None = None, **extra) -> dict:
    """게임 카드 (홈·검색·비슷한 게임 공통). 빈 값은 None 그대로 → 화면에서 대체 표시."""
    row = catalog.rows[game["id"]]
    data = {
        "id": game["id"],
        "name": game["name"],
        "genre": game["genre"],
        "tags": tag_names(game)[1:],
        "tier": row["tier"],
        "indie": game["indie"],
        "thumbnail_url": row["thumbnail_url"],
        "match": match_percent(game, profile) if profile is not None else None,
    }
    data.update(extra)
    return data


SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+")
SUMMARY_MAX = 120


def first_sentence(text: str | None) -> str | None:
    """게임 설명의 첫 줄·첫 문장 (랭킹 한 줄 설명, C11-c). 화면에서 한 줄 넘치면 말줄임"""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return None
    return SENTENCE_END.split(" ".join(lines[0].split()), maxsplit=1)[0][:SUMMARY_MAX]


# ---------- 추천 수·순위 ----------
def like_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT game_id, COUNT(*) AS n FROM reactions WHERE type = 'like' GROUP BY game_id")
    return {row["game_id"]: row["n"] for row in rows}


def like_count(conn: sqlite3.Connection, game_id: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM reactions WHERE type = 'like' AND game_id = ?", (game_id,)).fetchone()[0]


def ranking(conn: sqlite3.Connection, catalog: Catalog) -> list[dict]:
    """따봉 수 순위 (따봉 1개 이상인 게임만). 같은 수면 같은 순위 (1, 2, 2, 4 ...)."""
    order = {g["id"]: i for i, g in enumerate(catalog.games)}
    counts = sorted(((gid, n) for gid, n in like_counts(conn).items() if gid in order),
                    key=lambda x: (-x[1], order[x[0]]))
    result, rank = [], 0
    for i, (gid, n) in enumerate(counts):
        if i == 0 or n != counts[i - 1][1]:
            rank = i + 1
        result.append({"game_id": gid, "like_count": n, "rank": rank})
    return result


def rank_of(conn: sqlite3.Connection, catalog: Catalog, game_id: str) -> int | None:
    for item in ranking(conn, catalog):
        if item["game_id"] == game_id:
            return item["rank"]
    return None


# ---------- 비선호 연관 태그 (C11-e) ----------
_related_cache: dict = {"key": None, "table": {}}


def dislike_related() -> dict[str, list[str]]:
    """data/dislike_related.json ('종류:태그' → 같이 뺄 태그 목록). 파일이 바뀌면 다시 읽음, 없으면 빈 표"""
    path = config.DISLIKE_RELATED_PATH
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except FileNotFoundError:
        return {}
    if _related_cache["key"] != key:
        data = json.loads(path.read_text(encoding="utf-8"))
        _related_cache.update(key=key, table={k: list(v) for k, v in data.items() if not k.startswith("_")})
    return _related_cache["table"]


def expand_dislikes(features: set[str]) -> set[str]:
    """비선호 태그 + 연관 태그 (한 단계만, 연관 태그의 연관은 안 따라감)"""
    table = dislike_related()
    return set(features) | {r for f in features for r in table.get(f, [])}


# ---------- 실험 그룹별 취향 ----------
def profile_for(conn: sqlite3.Connection, user: sqlite3.Row, catalog: Catalog) -> UserProfile:
    """
    feedback: 현재 태그 점수 (설문 + 반응)
    survey / random: 설문 시작 점수만 (반응은 점수에 안 씀)
    이미 반응한 게임·좋아하는 게임을 다시 추천하지 않는 것(seen)과 비선호 제외는 세 그룹 모두 같다.
    비선호 제외는 연관 태그까지 넓힘 (C11-e, data/dislike_related.json) — 저장된 답은 그대로라 기존 사용자도 바로 적용
    """
    current = repo.load_profile(conn, user["id"])
    if user["exp_group"] == "feedback":
        profile = current
    else:
        profile = build_profile(repo.load_survey(conn, user["id"]), catalog.by_id)
        profile.seen = current.seen
    profile.excluded = expand_dislikes(profile.excluded)
    set_best_score(profile, catalog.games)  # 취향 %의 '1등 대비' 기준 (요청마다 한 번, C7-e)
    return profile


MIN_MATCH_FLOOR = 3  # min_match를 넘는 게임이 이보다 적으면 기준 없이 취향 높은 순 (C7-d)


def match_feed(conn: sqlite3.Connection, user: sqlite3.Row, catalog: Catalog, k: int,
               min_match: int = 0) -> tuple[list[dict], bool]:
    """
    (카드 목록, fallback). min_match > 0이면 취향 min_match% 이상 + 태그 HOME_MIN_TAGS개 이상만 남긴다
    (탐색 슬롯은 기준에서 뺌). 기준을 넘는 게임이 MIN_MATCH_FLOOR개 미만이면 기준 없이 원래 결과를 주고 fallback=True.
    """
    profile = profile_for(conn, user, catalog)

    def passes_home(game: dict) -> bool:  # 홈 슬라이드 기준 통과 칸 (C8-a: 태그 3개 이하 게임은 넣지 않음)
        return len(game_features(game)) >= HOME_MIN_TAGS and match_percent(game, profile) >= min_match

    if user["exp_group"] == "random":
        # 후보 거르기(장르·비선호·이미 본 게임)만 하고 순서는 무작위. 탐색 슬롯 없음
        candidates = [g for g in catalog.games if passes_filter(g, profile)]
        fallback = False
        if min_match > 0:
            passed = [g for g in candidates if passes_home(g)]
            fallback = len(passed) < MIN_MATCH_FLOOR
            if not fallback:
                candidates = passed
        picked = random.sample(candidates, min(k, len(candidates)))
        return [card(catalog, g, profile, slot="추천", reasons=reasons(g, profile)) for g in picked], fallback

    def cards(items: list[dict]) -> list[dict]:
        return [card(catalog, r["game"], profile, slot=r["slot"], reasons=r["reasons"]) for r in items]

    if min_match <= 0:
        return cards(recommend(catalog.games, profile, k=k)), False

    # 후보 전체를 점수 순으로 받아 기준 이상만 남기고, 탐색 슬롯(20%)은 남은 후보에서 엔진 규칙대로 다시 고른다
    ranked = recommend(catalog.games, profile, k=len(catalog.games), explore_ratio=0)
    n_explore = round(k * EXPLORE_RATIO)
    main = [r for r in ranked if passes_home(r["game"])][:k - n_explore]  # 순서는 score 그대로
    if len(main) < MIN_MATCH_FLOOR:
        return cards(recommend(catalog.games, profile, k=k)), True
    chosen = {r["game"]["id"] for r in main}
    rest = [g for g in catalog.games if g["id"] not in chosen]
    explore = recommend(rest, profile, k=n_explore, explore_ratio=1.0) if n_explore else []
    return cards(main + explore), False


# ---------- 취향 요약 ----------
def taste(conn: sqlite3.Connection, user_id: str) -> dict:
    """태그 종류 6개 축 = 점수가 있는 태그의 평균 (마이페이지 레이더 차트), 상·하위 태그"""
    rows = conn.execute(
        "SELECT t.category, t.name, s.score FROM user_tag_scores s JOIN tags t ON t.id = s.tag_id "
        "WHERE s.user_id = ? ORDER BY s.score DESC, t.id",
        (user_id,),
    ).fetchall()
    axes = []
    for category in FEATURE_FIELDS:
        scores = [row["score"] for row in rows if row["category"] == category]
        axes.append({"category": category, "average": round(sum(scores) / len(scores), 2) if scores else 0.0,
                     "count": len(scores)})
    tags = [{"category": row["category"], "name": row["name"], "score": row["score"]} for row in rows]
    return {
        "axes": axes,
        "top": [t for t in tags if t["score"] > 0][:5],
        "bottom": [t for t in reversed(tags) if t["score"] < 0][:5],
    }

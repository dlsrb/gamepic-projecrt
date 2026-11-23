"""
게임픽 MCP 도구의 실제 계산 (PROJECT.md 4.11 C13). MCP와 상관없는 보통 함수라 테스트에서 바로 부른다.

원칙 (4.1): AI는 이 도구 결과(게임픽 DB)만 근거로 답한다. 추천 결정은 기존 추천 엔진(app/reco)이 하고 AI는 묻고 설명만.
- DB는 읽기 전용으로 연다 (open_readonly: SQLite mode=ro + query_only). 쓰기 도구 없음
- 화면에 보이는 게임(review_status='ok')만. 사용자·댓글·반응 기록 같은 개인 데이터는 내보내지 않음
  (추천 순위의 '추천 수'처럼 모아 센 숫자만)
- 잘못된 태그·게임 id·탭은 예외 대신 {"error": 안내, ...쓸 수 있는 값} 으로 돌려줌 → AI가 다시 물을 수 있게
"""
import re
import sqlite3
from pathlib import Path

from app import config, rankings, services
from app.reco import survey_map
from app.reco.recommender import (
    FEATURE_FIELDS, UserProfile, build_profile, label_of, match_percent, recommend,
    set_best_score, similar_games,
)
from app.routers.search import WORD_MAX, WORDS_MAX, search_catalog

LIMIT_MAX = 30
SIMILAR_COUNT = 5
CATEGORY_LABELS = {"genre": "장르", "subgenre": "세부 장르", "view": "그래픽", "play": "플레이", "mood": "분위기", "bm": "과금"}
KINDS = {"popular": "인기 랭킹 (스토어 무료 차트)", "likes": "추천 순위 (게임픽 사용자 추천 수)",
         "monthly": "이달의 게임", "surge": "인기 급상승"}


# ---------- DB ----------
def open_readonly(db_path: Path | str | None = None) -> sqlite3.Connection:
    """읽기 전용 연결. 쓰기를 시도하면 sqlite3.OperationalError (attempt to write a readonly database)"""
    path = Path(db_path or config.DB_PATH).resolve()
    if not path.exists():
        raise FileNotFoundError(f"게임픽 DB가 없습니다: {path} (먼저 python scripts/init_db.py)")
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _limit(limit: int) -> int:
    return max(1, min(LIMIT_MAX, int(limit or 10)))


# ---------- 게임 한 줄 (AI가 근거로 쓸 내용) ----------
def game_brief(catalog: services.Catalog, game: dict, **extra) -> dict:
    row = catalog.rows[game["id"]]
    tags = {CATEGORY_LABELS[c]: ([game[c]] if c == "genre" else list(game[c])) for c in FEATURE_FIELDS}
    return {
        "id": game["id"],
        "name": game["name"],
        "genre": game["genre"],
        "tags": {k: v for k, v in tags.items() if v},
        "tag_source": row["tag_source"],          # human 사람 · ai AI 초안 승인 · store 스토어 분류 · auto 자동 규칙
        "tags_checked": catalog.checked(game),    # 사람·AI가 확인한 태그인지 (auto·store는 아님)
        "badges": catalog.badges.of(game["id"]),
        "store_links": {"google_play": row["android_url"], "app_store": row["ios_url"]},
        **extra,
    }


def _tag_index(conn: sqlite3.Connection) -> dict[str, str]:
    """태그 이름 → '종류:이름' (이름은 종류 사이에 겹치지 않음)"""
    return {r["name"]: f"{r['category']}:{r['name']}" for r in conn.execute("SELECT category, name FROM tags")}


def available_tags(conn: sqlite3.Connection) -> dict[str, list[str]]:
    rows = conn.execute("SELECT category, name FROM tags ORDER BY id").fetchall()
    return {label: [r["name"] for r in rows if r["category"] == c] for c, label in CATEGORY_LABELS.items()}


def _unknown_tags(conn, names: list[str]) -> dict | None:
    index = _tag_index(conn)
    unknown = [n for n in names if n not in index]
    if not unknown:
        return None
    return {"error": f"없는 태그: {', '.join(unknown)}. 아래 태그 이름만 쓸 수 있어요 (종류 없이 이름만).",
            "available_tags": available_tags(conn)}


# ---------- 1. 검색 ----------
def search_games(conn: sqlite3.Connection, query: str, limit: int = 10) -> dict:
    """게임픽 검색과 같은 해석: 단어마다 태그 / 동의어(무료·혼자 …) / 배지(신작·인디·인기·급상승) / 이름·태그 부분 일치, 모두 만족"""
    words = (query or "").split()
    if not words:
        return {"error": "검색어가 비었어요. 예: '힐링 퍼즐', '무료 2D', '인디', '스타듀'"}
    if len(words) > WORDS_MAX or any(len(w) > WORD_MAX for w in words):
        return {"error": f"검색어는 {WORDS_MAX}단어, 단어마다 {WORD_MAX}자까지예요."}
    catalog = services.Catalog(conn)
    profile = UserProfile()  # 사용자 없음 → 비선호 거르기 없음, 취향 % 없음
    terms, hits = search_catalog(catalog, profile, words, [])
    return {
        "query": " ".join(words),
        "interpreted_as": [{"word": t["word"], "kind": t["kind"], "meaning": t["label"]} for t in terms],
        "total": len(hits),
        "games": [game_brief(catalog, h["game"], name_exact=h["exact"]) for h in hits[:_limit(limit)]],
    }


# ---------- 2. 상세 ----------
def get_game(conn: sqlite3.Connection, game_id: str) -> dict:
    catalog = services.Catalog(conn)
    game = catalog.get(game_id or "")
    if game is None:
        needle = re.sub(r"[\W_]+", "", (game_id or "")).lower()
        close = [g for g in catalog.games if needle and needle in re.sub(r"[\W_]+", "", g["name"] + g["id"]).lower()][:5]
        return {"error": f"없는 게임 id: {game_id}. search_games로 이름을 검색해 id를 찾으세요.",
                "maybe": [{"id": g["id"], "name": g["name"]} for g in close]}
    row = catalog.rows[game_id]
    similar = [r for r in similar_games(game_id, catalog.games, k=len(catalog.games)) if r["game"]["id"] != game_id]
    return game_brief(
        catalog, game,
        developer=row["developer"],
        description=row["description"],
        release_date=row["release_date"],
        store_rating=row["store_rating"],
        store_rating_count=row["store_rating_count"],
        installs=row["installs"],
        size=row["tier"],
        chart_rank={"google_play": row["chart_rank_android"], "app_store": row["chart_rank_ios"]},
        similar_games=[{"id": r["game"]["id"], "name": r["game"]["name"], "genre": r["game"]["genre"],
                        "similarity": r["match"]} for r in similar[:SIMILAR_COUNT]],
    )


# ---------- 3. 추천 ----------
def _survey(conn, likes: list[str], dislikes: list[str], favorites: list[str]) -> dict:
    """태그 이름 → 설문 3·4·5번과 같은 엔진 입력 (장르를 좋아하면 그 장르만 후보, 세부 장르는 그 장르들도)"""
    index = _tag_index(conn)
    categories = sorted({index[n].split(":", 1)[0] for n in likes})
    engine_of = {o["value"]: o["engine"] for o in survey_map.tag_options(conn, categories, "like")}
    survey: dict = {"likes": {}, "favorites": list(favorites)}
    for name in likes:
        for key, values in engine_of[index[name]].items():
            if key.startswith("likes."):
                target = survey["likes"].setdefault(key.split(".", 1)[1], [])
            else:
                target = survey.setdefault(key, [])
            target.extend(v for v in values if v not in target)
    survey["dislikes"] = [index[n] for n in dislikes]
    return survey


def recommend_games(conn: sqlite3.Connection, likes: list[str], dislikes: list[str] | None = None,
                    favorite_game_ids: list[str] | None = None, limit: int = 10) -> dict:
    """기존 추천 엔진으로 추천. 비선호 + 연관 태그 제외, 태그 검수된 게임 먼저(모자라면 미검수로 채움, C12-i)"""
    likes, dislikes, favorites = list(likes or []), list(dislikes or []), list(favorite_game_ids or [])
    if not likes and not favorites:
        return {"error": "좋아하는 태그(likes)나 좋아하는 게임 id(favorite_game_ids)가 하나는 있어야 해요.",
                "available_tags": available_tags(conn)}
    problem = _unknown_tags(conn, likes + dislikes)
    if problem:
        return problem
    catalog = services.Catalog(conn)
    missing = [g for g in favorites if catalog.get(g) is None]
    if missing:
        return {"error": f"없는 게임 id: {', '.join(missing)}. search_games로 id를 찾으세요."}
    profile = build_profile(_survey(conn, likes, dislikes, favorites), catalog.by_id)
    profile.excluded = services.expand_dislikes(profile.excluded)
    set_best_score(profile, catalog.games)
    pool = services.recommendation_pool(catalog, profile)
    ranked = recommend(pool, profile, k=len(pool), explore_ratio=0)      # 무작위 탐색 칸 없이 (설명 가능하게)
    ranked.sort(key=lambda r: not catalog.checked(r["game"]))            # 검수된 게임 먼저, 채운 미검수는 뒤 (C12-i)
    ranked = ranked[:_limit(limit)]
    excluded_tags = sorted(label_of(f) for f in profile.excluded)
    return {
        "profile": {"likes": likes, "dislikes": dislikes, "excluded_with_related": excluded_tags,
                    "genre_filter": sorted(profile.genres), "favorites": favorites},
        "note": "추천 순서는 게임픽 추천 엔진(태그 가중 코사인)이 정한 것. match = 취향 %, reasons = 맞는 태그",
        "games": [game_brief(catalog, r["game"], match=match_percent(r["game"], profile), reasons=r["reasons"])
                  for r in ranked],
    }


# ---------- 4. 랭킹 ----------
def get_rankings(conn: sqlite3.Connection, kind: str = "popular", tab: str = "전체", limit: int = 10) -> dict:
    """kind: popular(인기 랭킹, tab = 전체·Google Play·App Store·장르 탭) | likes(추천 순위) | monthly(이달의 게임) | surge(급상승)"""
    if kind not in KINDS:
        return {"error": f"없는 kind: {kind}", "kinds": KINDS}
    catalog = services.Catalog(conn)
    n = _limit(limit)
    if kind == "popular":
        tabs = rankings.tabs()
        by_label = {t["label"]: t["key"] for t in tabs} | {t["key"]: t["key"] for t in tabs}
        if tab not in by_label:
            return {"error": f"없는 탭: {tab}", "tabs": [t["label"] for t in tabs]}
        rows = rankings.popular(catalog, by_label[tab], fill=True)[:n]
        return {"kind": KINDS[kind], "tab": tab,
                "note": "rank가 null이면 스토어 차트 밖 (설치 수·별점 수 순으로 채운 것)",
                "games": [game_brief(catalog, g, rank=rank) for rank, g in rows]}
    if kind == "likes":
        items = services.ranking(conn, catalog)[:n]
        return {"kind": KINDS[kind], "games": [game_brief(catalog, catalog.get(i["game_id"]), rank=i["rank"],
                                                          like_count=i["like_count"]) for i in items]}
    if kind == "monthly":
        picked = rankings.monthly(catalog, UserProfile(), size=n)  # 사용자 없음 → 비선호·취향 없이
        return {"kind": KINDS[kind], "games": [game_brief(catalog, p["game"], month_rank=p["month_rank"])
                                               for p in picked]}
    book = catalog.badges
    if book.collecting:
        return {"kind": KINDS[kind], "games": [],
                "note": "급상승은 하루 1번 저장하는 기록이 7일 넘게 쌓여야 계산돼요. 지금은 기록 쌓는 중"}
    top = sorted(book.surge.items(), key=lambda x: -x[1])[:n]
    return {"kind": KINDS[kind], "games": [game_brief(catalog, catalog.get(gid), surge_score=score) for gid, score in top]}

"""검색 페이지: 검색, 자동완성, 추천 검색어, 태그 목록 (4.10, 4.11 C7-c)."""
import json
import re
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import BASE_DIR
from app.db import get_db
from app.deps import get_user
from app.reco.recommender import FEATURE_FIELDS, game_features, label_of
from app.security import rate_limit
from app.services import Catalog, card, profile_for, tag_names

router = APIRouter(prefix="/api", tags=["검색"])

QUERY_MAX = 60                     # 검색어 전체 (띄어쓰기로 여러 단어)
WORD_MAX, WORDS_MAX = 30, 10       # 단어 하나 길이, 단어 수
TAGS_PARAM_MAX, TAGS_MAX = 330, 10
RESULT_MAX = 30
SUGGEST_MAX = 8
POPULAR_DAYS, POPULAR_MAX = 7, 10
SYNONYMS_PATH = BASE_DIR / "data" / "search_synonyms.json"


def _parse_tags(tags: str) -> list[str]:
    names = [t.strip() for t in tags.split(",") if t.strip()]
    if len(names) > TAGS_MAX or any(len(t) > WORD_MAX for t in names):
        raise HTTPException(422, f"키워드는 {TAGS_MAX}개, 각 {WORD_MAX}자까지입니다.")
    return names


def _normalize(text: str) -> str:
    """비교용: 소문자, 띄어쓰기·기호 제거 ("쿠키런: 킹덤" → "쿠키런킹덤")"""
    return re.sub(r"[\W_]+", "", text).lower()


def _synonyms() -> dict:
    return json.loads(SYNONYMS_PATH.read_text(encoding="utf-8"))["words"]


def interpret(words: list[str], all_tags: set[str]) -> list[dict]:
    """
    단어마다 해석 (C7-c): ① 태그 이름과 같으면 태그 ② 동의어 사전 ③ 아니면 이름·태그 부분 일치.
    반환: [{word, kind: tag|synonym|text, label, include, exclude}]
    """
    synonyms = _synonyms()
    by_lower = {t.lower(): t for t in all_tags}
    terms = []
    for word in words:
        lower = word.lower()
        if lower in by_lower:
            terms.append({"word": word, "kind": "tag", "label": by_lower[lower],
                          "include": [by_lower[lower]], "exclude": []})
        elif word in synonyms:
            entry = synonyms[word]
            terms.append({"word": word, "kind": "synonym", "label": entry.get("label", word),
                          "include": entry.get("include", []), "exclude": entry.get("exclude", [])})
        else:
            terms.append({"word": word, "kind": "text", "label": word, "include": [], "exclude": []})
    return terms


def _matches(game: dict, terms: list[dict]) -> bool:
    """모든 단어를 만족해야 (AND)"""
    names = set(tag_names(game))
    features = set(game_features(game))
    for term in terms:
        if term["kind"] == "tag" and not set(term["include"]) <= names:
            return False
        if term["kind"] == "synonym" and (not set(term["include"]) <= features or set(term["exclude"]) & features):
            return False
        if term["kind"] == "text":
            needle = _normalize(term["word"])
            if needle not in _normalize(game["name"]) and not any(needle in _normalize(n) for n in names):
                return False
    return True


@router.get("/search", dependencies=[Depends(rate_limit("search"))])
def search(q: str = Query("", max_length=QUERY_MAX), tags: str = Query("", max_length=TAGS_PARAM_MAX),
           user=Depends(get_user), conn: sqlite3.Connection = Depends(get_db)):
    """
    q: 띄어쓰기로 나눈 단어마다 태그 / 동의어(data/search_synonyms.json) / 이름·태그 부분 일치로 해석, 모두 만족(AND)
       예: "무료 2D" = 유료구매가 아니면서 2D인 게임
    tags: 쉼표로 구분한 태그 이름 (모두 가진 게임만, 예전 방식도 그대로 받음)
    검색어 전체가 게임 이름과 같으면 그 게임이 맨 앞, 나머지는 취향 부합도 높은 순.
    비선호(+연관 태그, C11-e)가 붙은 게임은 뺀다. 단 이름을 정확히 친 게임, 직접 검색한 태그 때문에 걸리는 경우는 남김
    검색어는 search_logs에 남긴다 (사용자 ID 없이).
    """
    words = q.split()
    if len(words) > WORDS_MAX or any(len(w) > WORD_MAX for w in words):
        raise HTTPException(422, f"검색어는 {WORDS_MAX}단어, 단어마다 {WORD_MAX}자까지입니다.")
    q = " ".join(words)
    selected = _parse_tags(tags)
    if not words and not selected:
        return {"q": q, "tags": selected, "keywords": [], "games": []}

    catalog = Catalog(conn)
    profile = profile_for(conn, user, catalog)
    all_tags = {t for g in catalog.games for t in tag_names(g)}
    terms = interpret(words, all_tags) + interpret(selected, all_tags)
    exact = _normalize(q)
    searched = {n for t in terms if t["kind"] == "tag" for n in t["include"]}
    searched |= {label_of(f) for t in terms if t["kind"] == "synonym" for f in t["include"]}
    excluded = {f for f in profile.excluded if label_of(f) not in searched}
    found = []
    for game in catalog.games:
        if not _matches(game, terms):
            continue
        is_exact = bool(exact) and _normalize(game["name"]) == exact
        if not is_exact and set(game_features(game)) & excluded:
            continue
        found.append(card(catalog, game, profile, exact=is_exact))
    found.sort(key=lambda c: (not c["exact"], -c["match"]))

    if q:
        conn.execute("INSERT INTO search_logs (keyword) VALUES (?)", (q,))
    keywords = [{k: t[k] for k in ("word", "kind", "label")} for t in terms]
    return {"q": q, "tags": selected, "keywords": keywords, "games": found[:RESULT_MAX]}


@router.get("/search/suggest", dependencies=[Depends(rate_limit("suggest"))])
def suggest(q: str = Query(..., min_length=1, max_length=QUERY_MAX), conn: sqlite3.Connection = Depends(get_db)):
    """자동완성: 게임 이름·태그 이름 앞부분 일치, 최대 8개 (게임 먼저)"""
    prefix = " ".join(q.split()).lower()
    if not prefix:
        return {"suggestions": []}
    items = [{"type": "game", "text": row["name"], "id": row["id"]}
             for row in conn.execute("SELECT id, name FROM games WHERE review_status = 'ok' ORDER BY rowid")
             if row["name"].lower().startswith(prefix)]
    items += [{"type": "tag", "text": row["name"], "category": row["category"]}
              for row in conn.execute("SELECT DISTINCT name, category FROM tags ORDER BY id")
              if row["name"].lower().startswith(prefix)]
    return {"suggestions": items[:SUGGEST_MAX]}


@router.get("/search/popular")
def popular_keywords(conn: sqlite3.Connection = Depends(get_db)):
    """추천 검색어: 최근 7일 많이 검색된 말 → 모자라면 게임이 많은 태그로 채움 (최대 10개).
    사용자별 기록이 아니다 (내 기록 = '최근 검색어'는 브라우저에만 저장)."""
    rows = conn.execute(
        """
        SELECT keyword, COUNT(*) AS n FROM search_logs
        WHERE created_at >= datetime('now', ?)
        GROUP BY keyword ORDER BY n DESC, MAX(id) DESC LIMIT ?
        """,
        (f"-{POPULAR_DAYS} days", POPULAR_MAX),
    )
    keywords = [{"keyword": row["keyword"], "count": row["n"], "source": "search"} for row in rows]
    if len(keywords) < POPULAR_MAX:
        used = {k["keyword"] for k in keywords}
        tag_rows = conn.execute(
            "SELECT t.name, COUNT(gt.game_id) AS n FROM tags t JOIN game_tags gt ON gt.tag_id = t.id "
            "GROUP BY t.id ORDER BY n DESC, t.id LIMIT ?", (POPULAR_MAX * 2,))
        for row in tag_rows:
            if len(keywords) >= POPULAR_MAX:
                break
            if row["name"] not in used:
                keywords.append({"keyword": row["name"], "count": None, "source": "tag"})
                used.add(row["name"])
    return {"keywords": keywords}


@router.get("/tags")
def tags(conn: sqlite3.Connection = Depends(get_db)):
    """태그 목록 (종류별, 그 태그를 가진 게임 수 포함)"""
    rows = conn.execute(
        "SELECT t.category, t.name, COUNT(gt.game_id) AS n FROM tags t "
        "LEFT JOIN game_tags gt ON gt.tag_id = t.id GROUP BY t.id ORDER BY t.id"
    )
    result = {c: [] for c in FEATURE_FIELDS}
    for row in rows:
        result[row["category"]].append({"name": row["name"], "game_count": row["n"]})
    return {"tags": result}

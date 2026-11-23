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
    반환: [{word, kind: tag|synonym|badge|text, label, include, exclude}]
    """
    synonyms = _synonyms()
    by_lower = {t.lower(): t for t in all_tags}
    terms = []
    for word in words:
        lower = word.lower()
        if lower in by_lower:
            terms.append({"word": word, "kind": "tag", "label": by_lower[lower],
                          "include": [by_lower[lower]], "exclude": []})
        elif word in synonyms and synonyms[word].get("badge"):  # 자동 배지 (C12-e): "신작"·"인디"·"인기"·"급상승"
            entry = synonyms[word]
            terms.append({"word": word, "kind": "badge", "label": entry.get("label", word),
                          "include": [entry["badge"]], "exclude": []})
        elif word in synonyms:
            entry = synonyms[word]
            terms.append({"word": word, "kind": "synonym", "label": entry.get("label", word),
                          "include": entry.get("include", []), "exclude": entry.get("exclude", [])})
        else:
            terms.append({"word": word, "kind": "text", "label": word, "include": [], "exclude": []})
    return terms


def _matches(game: dict, terms: list[dict], badges=None) -> bool:
    """모든 단어를 만족해야 (AND). badges = BadgeBook (자동 배지 단어가 있을 때)"""
    names = set(tag_names(game))
    features = set(game_features(game))
    for term in terms:
        if term["kind"] == "badge" and not (badges and badges.has(game["id"], term["include"][0])):
            return False
        if term["kind"] == "tag" and not set(term["include"]) <= names:
            return False
        if term["kind"] == "synonym" and (not set(term["include"]) <= features or set(term["exclude"]) & features):
            return False
        if term["kind"] == "text":
            needle = _normalize(term["word"])
            if needle not in _normalize(game["name"]) and not any(needle in _normalize(n) for n in names):
                return False
    return True


NAME_EXACT, NAME_PREFIX, NAME_PART, NAME_NONE = 0, 1, 2, 3


def _name_rank(name: str, whole: str, needles: list[str]) -> int | None:
    """검색어와 게임 이름(둘 다 _normalize)의 관계: 정확히 0 · 앞부분 1 · 부분 2 · 이름과 무관 None (C12-k)"""
    if whole and name == whole:
        return NAME_EXACT
    words = [w for w in [whole, *needles] if w]
    if any(name.startswith(w) for w in words):
        return NAME_PREFIX
    if any(w in name for w in words):
        return NAME_PART
    return None


@router.get("/search", dependencies=[Depends(rate_limit("search"))])
def search(q: str = Query("", max_length=QUERY_MAX), tags: str = Query("", max_length=TAGS_PARAM_MAX),
           user=Depends(get_user), conn: sqlite3.Connection = Depends(get_db)):
    """
    q: 띄어쓰기로 나눈 단어마다 태그 / 동의어(data/search_synonyms.json) / 이름·태그 부분 일치로 해석, 모두 만족(AND)
       예: "무료 2D" = 유료구매가 아니면서 2D인 게임
    tags: 쉼표로 구분한 태그 이름 (모두 가진 게임만, 예전 방식도 그대로 받음)
    순서: 이름 정확히 일치 → 이름 앞부분 일치 → 이름 부분 일치 → 나머지, 각 묶음 안에서는 취향 부합도 높은 순 (C12-k).
    비선호(+연관 태그, C11-e)가 붙은 게임은 뺀다. 단 검색어가 게임 이름에 들어 있으면(이름 부분 일치) 남기고
    카드에 disliked=true (C12-k), 직접 검색한 태그 때문에 걸리는 경우도 남김. 즉 비선호 제외는 태그·동의어·배지로 찾은 결과에만
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
    needles = [n for n in (_normalize(t["word"]) for t in terms if t["kind"] == "text") if n]
    found = []
    for game in catalog.games:
        if not _matches(game, terms, catalog.badges):
            continue
        name = _normalize(game["name"])
        name_rank = _name_rank(name, exact, needles)
        disliked = bool(set(game_features(game)) & profile.excluded)
        if name_rank is None and set(game_features(game)) & excluded:
            continue  # 태그·동의어·배지로만 찾은 비선호 게임은 뺌
        extra = {"disliked": True} if disliked and name_rank is not None else {}
        found.append((name_rank if name_rank is not None else NAME_NONE,
                      card(catalog, game, profile, exact=name_rank == NAME_EXACT, **extra)))
    found.sort(key=lambda x: (x[0], -x[1]["match"]))
    found = [c for _, c in found]

    if q:
        conn.execute("INSERT INTO search_logs (keyword) VALUES (?)", (q,))
    keywords = [{k: t[k] for k in ("word", "kind", "label")} for t in terms]
    return {"q": q, "tags": selected, "keywords": keywords, "games": found[:RESULT_MAX]}


@router.get("/search/suggest", dependencies=[Depends(rate_limit("suggest"))])
def suggest(q: str = Query(..., min_length=1, max_length=QUERY_MAX), conn: sqlite3.Connection = Depends(get_db)):
    """
    자동완성 (C12-k: 검색과 같은 이름 규칙): 게임 이름은 띄어쓰기·기호·대소문자 무시하고 앞부분 일치 먼저 → 부분 일치,
    그다음 태그 이름 앞부분 일치, 최대 8개 (게임 먼저). 비선호로 거르지 않음 (이름으로 찾는 것이라 검색과 같음)
    """
    prefix = " ".join(q.split()).lower()
    needle = _normalize(q)
    if not prefix or not needle:
        return {"suggestions": []}
    games = [(_normalize(row["name"]), row) for row in
             conn.execute("SELECT id, name FROM games WHERE review_status = 'ok' ORDER BY rowid")]
    items = [{"type": "game", "text": row["name"], "id": row["id"]}
             for name, row in sorted(((n, r) for n, r in games if needle in n), key=lambda x: not x[0].startswith(needle))]
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

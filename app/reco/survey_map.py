"""
설문 문항(data/survey.json) ↔ 추천 엔진 설문 형식 (PROJECT.md 4.12, 4.11 C4-설문).
- 문항 보기 채우기: 태그·게임 보기는 DB에서 (게임이 늘면 보기도 자동으로 늘어남)
- 응답 검사: 보기에 있는 값만 / 선택 개수(min·max) / 필수 / 조건부 문항(show_if) / "없어요"처럼 혼자만 고를 수 있는 보기
- 응답 → 엔진 형식 변환은 to_engine() 한 곳에서. 보기마다 붙은 "engine" 값을 모은다

보기의 engine 값 (survey.json에 적는 방법):
  "genres" / "subgenres" / "dislikes" / "favorites" : 목록에 추가
  "likes.view" 등 "likes.종류"                       : likes[종류]에 추가
  "require" : 이 태그('종류:태그')가 없는 게임은 후보에서 제외 (예: 꼭 오프라인)
  "boost"   : 점수만 더함, 장르 필터에는 안 넣음 (예: 두뇌 → genre:퍼즐)
  그 밖의 키(platform, max_price, ai_ok, difficulty) : 값 그대로 저장만 (게임 데이터가 생기면 필터 추가)
"""
import json
import sqlite3

from app import config

LIST_KEYS = ("genres", "subgenres", "dislikes", "favorites", "require", "boost")


# ---------- 문항 ----------
def load_questions() -> list[dict] | None:
    """data/survey.json의 문항. 파일이 없으면 None."""
    if not config.SURVEY_PATH.exists():
        return None
    return json.loads(config.SURVEY_PATH.read_text(encoding="utf-8"))["questions"]


def _tag_genres(conn: sqlite3.Connection) -> dict[tuple[str, str], list[str]]:
    """(종류, 태그) → 그 태그를 가진 게임들의 장르 목록"""
    rows = conn.execute(
        """
        SELECT t.category, t.name, g.name AS genre FROM tags t
        JOIN game_tags gt ON gt.tag_id = t.id
        JOIN game_tags gg ON gg.game_id = gt.game_id
        JOIN tags g ON g.id = gg.tag_id AND g.category = 'genre'
        ORDER BY t.id, gg.rowid
        """
    )
    result: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        genres = result.setdefault((row["category"], row["name"]), [])
        if row["genre"] not in genres:
            genres.append(row["genre"])
    return result


def _tag_engine(category: str, name: str, genres: list[str], as_: str) -> dict:
    if as_ == "dislike":
        return {"dislikes": [f"{category}:{name}"]}
    if category == "genre":
        return {"genres": [name]}
    if category == "subgenre":
        # 세부 장르만 골라도 그 세부 장르를 가진 게임들의 장르를 장르 필터에 넣는다 (4.12 3번)
        return {"subgenres": [name], "genres": list(genres)}
    return {f"likes.{category}": [name]}


def tag_options(conn: sqlite3.Connection, categories: list[str], as_: str) -> list[dict]:
    genres_of = _tag_genres(conn)
    options = []
    for category in categories:
        for row in conn.execute("SELECT name FROM tags WHERE category = ? ORDER BY id", (category,)):
            genres = genres_of.get((category, row["name"]), [])
            options.append({"label": row["name"], "value": f"{category}:{row['name']}", "category": category,
                            "genres": genres, "engine": _tag_engine(category, row["name"], genres, as_)})
    return options


def game_options(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT g.id, g.name, g.thumbnail_url, t.name AS genre FROM games g
        JOIN game_tags gt ON gt.game_id = g.id
        JOIN tags t ON t.id = gt.tag_id AND t.category = 'genre'
        WHERE g.source = 'manual' AND g.review_status = 'ok'  -- 설문 보기는 직접 고른 게임만 (자동 수집이 늘어도 고정)
        ORDER BY g.rowid
        """
    )
    return [{"label": row["name"], "value": row["id"], "genres": [row["genre"]],
             "thumbnail_url": row["thumbnail_url"], "engine": {"favorites": [row["id"]]}} for row in rows]


def resolve(conn: sqlite3.Connection, question: dict) -> dict:
    """문항 하나의 보기를 실제 목록으로 채운다 (DB 보기 + extra_options)."""
    spec = question.get("options", [])
    if isinstance(spec, list):
        options = [dict(o) for o in spec]
    elif spec.get("from") == "tags":
        options = tag_options(conn, spec["categories"], spec.get("as", "like"))
    elif spec.get("from") == "games":
        options = game_options(conn)
    else:
        raise ValueError(f"알 수 없는 options: {spec}")
    resolved = {k: v for k, v in question.items() if k not in ("options", "extra_options")}
    resolved["options"] = options + [dict(o) for o in question.get("extra_options", [])]
    return resolved


def resolved_questions(conn: sqlite3.Connection) -> list[dict] | None:
    questions = load_questions()
    return None if questions is None else [resolve(conn, q) for q in questions]


def public(question: dict) -> dict:
    """화면에 보낼 모양: 엔진 반영 방법(engine)은 빼고 보낸다."""
    return {**question, "options": [{k: v for k, v in o.items() if k != "engine"} for o in question["options"]]}


# ---------- 응답 검사 ----------
def is_shown(question: dict, answers: dict[str, list[str]]) -> bool:
    """조건부 문항: show_if의 문항에서 지정한 보기 중 하나를 골랐을 때만 보인다."""
    for qid, values in question.get("show_if", {}).items():
        if not set(answers.get(qid, [])) & set(values):
            return False
    return True


def validate(questions: list[dict], answers: dict[str, list[str]]) -> list[str]:
    """문제 목록 (비었으면 통과)"""
    problems = []
    known = {q["id"] for q in questions}
    unknown = [qid for qid in answers if qid not in known]
    if unknown:
        problems.append(f"없는 문항: {', '.join(unknown)}")
    for q in questions:
        chosen = answers.get(q["id"])
        if not is_shown(q, answers):
            if chosen:
                problems.append(f"{q['id']}: 지금은 답할 수 없는 문항입니다")
            continue
        if chosen is None:
            if q.get("required"):
                problems.append(f"{q['id']}: 필수 문항입니다")
            continue
        options = {o["value"]: o for o in q["options"]}
        bad = [v for v in chosen if v not in options]
        if bad:
            problems.append(f"{q['id']}: 없는 보기 {', '.join(bad)}")
            continue
        if len(set(chosen)) != len(chosen):
            problems.append(f"{q['id']}: 같은 보기를 두 번 골랐습니다")
        low, high = q.get("min", 0), q.get("max")
        if len(chosen) < low or (high is not None and len(chosen) > high):
            problems.append(f"{q['id']}: {low}~{high if high is not None else ''}개 골라 주세요")
        if len(chosen) > 1 and any(options[v].get("exclusive") for v in chosen):
            problems.append(f"{q['id']}: '{next(options[v]['label'] for v in chosen if options[v].get('exclusive'))}'는 "
                            "다른 보기와 함께 고를 수 없습니다")
    return problems


# ---------- 응답 → 엔진 형식 ----------
def to_engine(questions: list[dict], answers: dict[str, list[str]]) -> dict:
    """설문 응답(문항 id → 고른 보기 값) → 추천 엔진 설문 형식 (recommender.build_profile 입력)"""
    survey: dict = {"likes": {}}
    for q in questions:
        if not is_shown(q, answers):
            continue
        options = {o["value"]: o for o in q["options"]}
        for value in answers.get(q["id"], []):
            for key, mapped in options.get(value, {}).get("engine", {}).items():
                if key.startswith("likes."):
                    target = survey["likes"].setdefault(key.split(".", 1)[1], [])
                elif key in LIST_KEYS:
                    target = survey.setdefault(key, [])
                else:
                    survey[key] = mapped  # 저장만 하는 값 (platform 등)
                    continue
                target.extend(m for m in mapped if m not in target)
    return survey

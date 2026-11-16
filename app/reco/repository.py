"""
추천 엔진 ↔ DB 연결 (PROJECT.md 4.9, 4.11 C3, C3-보완).
- 게임·태그를 DB에서 읽어 recommender.py가 쓰는 형식으로 바꾼다
- 설문 응답 → 태그 시작 점수 저장, DB 점수 → UserProfile
- 반응 기록 + 태그 점수 갱신 (남용 방지 규칙 포함)
- 점수 재계산: 설문 시작 점수 + 남은 반응을 순서대로 다시 적용 (따봉 취소, 비선호 변경 때)
모든 함수는 열린 연결(conn)을 받는다. commit·close는 호출한 쪽(app/db.py)이 한다.
"""
import json

from app.reco import survey_map
from app.reco.reco_config import FEEDBACK_DELTA, INITIAL_SCORE
from app.reco.recommender import (
    FEATURE_FIELDS, UserProfile, apply_feedback, build_profile, category_of, clamp, label_of,
)

LIST_FIELDS = [c for c in FEATURE_FIELDS if c != "genre"]
# survey_answers의 question_id가 이 이름이면 엔진 형식 그대로 저장된 값 (테스트·설정의 비선호 관리),
# 그 밖(q1, q2_1 ...)은 설문 문항 응답 원본 → survey_map.to_engine()으로 변환
SURVEY_KEYS = ("genres", "subgenres", "likes", "dislikes", "favorites", "boost", "require")
SEEN_TYPES = ("like", "store", "dislike")  # 이 반응을 남긴 게임은 다시 추천하지 않는다
_SEEN_PLACEHOLDERS = ", ".join("?" * len(SEEN_TYPES))


# ---------- 게임 ----------
def load_games(conn) -> list[dict]:
    """
    프로토타입 games.json과 같은 형식: id, name, genre(문자열), subgenre/view/play/mood/bm(목록), indie
    검수 대기·제외(review_status pending/rejected)인 자동 수집 게임과 장르가 없는 게임은 뺀다 (C7-a2).
    indie = is_indie (규모와 따로, 개발사 기준)
    """
    games = {}
    for row in conn.execute("SELECT id, name, is_indie FROM games WHERE review_status = 'ok' ORDER BY rowid"):
        games[row["id"]] = {"id": row["id"], "name": row["name"], "genre": None,
                            **{c: [] for c in LIST_FIELDS}, "indie": row["is_indie"] == 1}
    rows = conn.execute(
        "SELECT gt.game_id, t.name, t.category FROM game_tags gt JOIN tags t ON t.id = gt.tag_id ORDER BY gt.rowid"
    )
    for row in rows:
        game = games.get(row["game_id"])
        if game is None:
            continue
        if row["category"] == "genre":
            game["genre"] = row["name"]
        else:
            game[row["category"]].append(row["name"])
    return [g for g in games.values() if g["genre"]]


def _tag_ids(conn, features) -> dict[str, int]:
    """'종류:태그' → tags.id. DB에 없는 태그는 빠진다."""
    ids = {}
    for f in features:
        row = conn.execute("SELECT id FROM tags WHERE category = ? AND name = ?",
                           (category_of(f), label_of(f))).fetchone()
        if row:
            ids[f] = row["id"]
    return ids


# ---------- 설문 ----------
def _replace_answers(conn, user_id: str, rows: dict) -> None:
    """설문 응답을 통째로 바꾸고 점수 재계산. 남아 있는 반응 기록도 함께 다시 적용된다
    (점수 = 설문 시작 점수 + 남은 반응)."""
    conn.execute("DELETE FROM survey_answers WHERE user_id = ?", (user_id,))
    conn.executemany(
        "INSERT INTO survey_answers (user_id, question_id, answer) VALUES (?, ?, ?)",
        [(user_id, key, json.dumps(value, ensure_ascii=False)) for key, value in rows.items()],
    )
    conn.execute("UPDATE users SET survey_done = 1 WHERE id = ?", (user_id,))
    recompute_scores(conn, user_id)


def save_answers(conn, user_id: str, answers: dict[str, list[str]]) -> None:
    """설문 페이지 응답 원본(문항 id → 고른 보기 값) 저장. 검사는 survey_map.validate()로 먼저 한다.
    설정의 '설문 다시 하기'도 이걸 쓴다 (설정에서 바꾼 비선호도 설문 응답으로 다시 정해짐)."""
    _replace_answers(conn, user_id, answers)


def save_survey(conn, user_id: str, survey: dict) -> None:
    """엔진 형식 설문을 그대로 저장 (테스트·개발용)"""
    _replace_answers(conn, user_id, {key: survey[key] for key in SURVEY_KEYS if key in survey})


def load_answers(conn, user_id: str) -> dict:
    """survey_answers 원본 그대로 (문항 응답 + 엔진 형식 값)"""
    rows = conn.execute("SELECT question_id, answer FROM survey_answers WHERE user_id = ?", (user_id,))
    return {row["question_id"]: json.loads(row["answer"]) for row in rows}


def load_survey(conn, user_id: str) -> dict:
    """엔진 형식 설문. 문항 응답을 변환한 뒤, 엔진 형식으로 저장된 값이 있으면 그 키는 그것으로 덮어쓴다
    (예: 설정에서 바꾼 비선호 'dislikes'가 설문 2·4·6번에서 나온 비선호를 대신함)."""
    rows = load_answers(conn, user_id)
    answers = {k: v for k, v in rows.items() if k not in SURVEY_KEYS}
    survey = {}
    if answers:
        questions = survey_map.resolved_questions(conn)
        if questions is not None:
            survey = survey_map.to_engine(questions, answers)
    survey.update({k: v for k, v in rows.items() if k in SURVEY_KEYS})
    return survey


def set_disliked_tags(conn, user_id: str, features: list[str]) -> None:
    """설정의 '비선호 태그 관리': 설문의 dislikes 응답만 바꾸고 점수를 재계산한다."""
    conn.execute(
        """
        INSERT INTO survey_answers (user_id, question_id, answer) VALUES (?, 'dislikes', ?)
        ON CONFLICT (user_id, question_id) DO UPDATE SET answer = excluded.answer
        """,
        (user_id, json.dumps(features, ensure_ascii=False)),
    )
    recompute_scores(conn, user_id)


def load_profile(conn, user_id: str) -> UserProfile:
    p = UserProfile()
    survey = load_survey(conn, user_id)
    p.genres = set(survey.get("genres", []))
    p.seen = set(survey.get("favorites", []))
    # 후보 제외는 사용자가 직접 고른 비선호만. 관심 없음으로 −5가 된 태그는 순위만 내려간다.
    p.excluded = set(survey.get("dislikes", []))
    p.required = set(survey.get("require", []))

    rows = conn.execute(
        "SELECT t.category, t.name, s.score FROM user_tag_scores s JOIN tags t ON t.id = s.tag_id WHERE s.user_id = ?",
        (user_id,),
    )
    for row in rows:
        p.weights[f"{row['category']}:{row['name']}"] = row["score"]

    rows = conn.execute(
        f"SELECT DISTINCT game_id FROM reactions WHERE user_id = ? AND type IN ({_SEEN_PLACEHOLDERS})",
        (user_id, *SEEN_TYPES),
    )
    p.seen |= {row["game_id"] for row in rows}
    return p


# ---------- 점수 재계산 ----------
def recompute_scores(conn, user_id: str) -> None:
    """설문 시작 점수에서 출발해 남은 반응을 created_at, id 순으로 다시 적용하고 user_tag_scores를 통째로 바꿔 쓴다.
    규칙은 record_reaction과 같다: 매 단계 ±5 자르기 / 따봉 게임당 1회 / 스토어는 같은 게임·같은 날(로컬) 첫 기록만."""
    games_by_id = {g["id"]: g for g in load_games(conn)}
    profile = build_profile(load_survey(conn, user_id), games_by_id)

    liked, stored = set(), set()
    rows = conn.execute(
        f"""
        SELECT game_id, type, date(created_at, 'localtime') AS day FROM reactions
        WHERE user_id = ? AND type IN ({_SEEN_PLACEHOLDERS}) ORDER BY created_at, id
        """,
        (user_id, *SEEN_TYPES),
    )
    for row in rows:
        game = games_by_id.get(row["game_id"])
        if game is None:
            continue
        if row["type"] == "like":
            if row["game_id"] in liked:
                continue
            liked.add(row["game_id"])
        elif row["type"] == "store":
            if (row["game_id"], row["day"]) in stored:
                continue
            stored.add((row["game_id"], row["day"]))
        apply_feedback(profile, game, row["type"])

    tag_ids = _tag_ids(conn, profile.weights)
    conn.execute("DELETE FROM user_tag_scores WHERE user_id = ?", (user_id,))
    conn.executemany(
        "INSERT INTO user_tag_scores (user_id, tag_id, score) VALUES (?, ?, ?)",
        [(user_id, tag_ids[f], w) for f, w in profile.weights.items() if f in tag_ids],
    )


# ---------- 반응 → 점수 갱신 ----------
def _add_to_game_tags(conn, user_id: str, game_id: str, delta: float) -> None:
    """게임이 가진 태그 점수에 delta를 더한다 (범위 밖은 잘라냄)."""
    rows = conn.execute(
        """
        SELECT gt.tag_id, s.score FROM game_tags gt
        LEFT JOIN user_tag_scores s ON s.tag_id = gt.tag_id AND s.user_id = ?
        WHERE gt.game_id = ?
        """,
        (user_id, game_id),
    ).fetchall()
    conn.executemany(
        """
        INSERT INTO user_tag_scores (user_id, tag_id, score) VALUES (?, ?, ?)
        ON CONFLICT (user_id, tag_id) DO UPDATE SET score = excluded.score
        """,
        [(user_id, row["tag_id"],
          clamp((INITIAL_SCORE if row["score"] is None else row["score"]) + delta)) for row in rows],
    )


def _has_reaction(conn, user_id: str, game_id: str, type_: str, today_only: bool = False) -> bool:
    sql = "SELECT 1 FROM reactions WHERE user_id = ? AND game_id = ? AND type = ?"
    if today_only:
        sql += " AND date(created_at, 'localtime') = date('now', 'localtime')"
    return conn.execute(sql, (user_id, game_id, type_)).fetchone() is not None


def record_reaction(conn, user_id: str, game_id: str, type_: str) -> bool:
    """반응 기록. 태그 점수가 바뀌었으면 True. 빠르게 바로 더하지만, 결과는 recompute_scores와 같다.
    - like: 게임당 1회. 이미 있으면 기록도 점수도 그대로
    - store: 기록은 매번 남기고(랭킹용), 점수는 게임당 하루 1회만
    - dislike: 매번 반영
    - view: 기록만 (최근 본 게임)
    """
    if type_ == "like" and _has_reaction(conn, user_id, game_id, "like"):
        return False
    applies = type_ in FEEDBACK_DELTA
    if type_ == "store" and _has_reaction(conn, user_id, game_id, "store", today_only=True):
        applies = False

    conn.execute("INSERT INTO reactions (user_id, game_id, type) VALUES (?, ?, ?)", (user_id, game_id, type_))
    if applies:
        _add_to_game_tags(conn, user_id, game_id, FEEDBACK_DELTA[type_])
    return applies


def cancel_like(conn, user_id: str, game_id: str) -> bool:
    """따봉 취소: like 행을 지우고 점수를 재계산한다 (상한에 걸렸던 경우도 정확히 원래대로). 따봉이 없었으면 False."""
    cur = conn.execute("DELETE FROM reactions WHERE user_id = ? AND game_id = ? AND type = 'like'",
                       (user_id, game_id))
    if cur.rowcount == 0:
        return False
    recompute_scores(conn, user_id)
    return True

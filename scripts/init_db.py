"""DB 생성 + 게임 데이터 적재 (PROJECT.md 4.9, 4.11 C2).

실행: python scripts/init_db.py
여러 번 실행해도 테이블·행이 중복되지 않는다.
"""
import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.config import DB_PATH  # noqa: E402
from app.db import get_connection  # noqa: E402

GAMES_JSON = BASE_DIR / "gamepick_reco" / "data" / "games.json"
LIST_CATEGORIES = ("subgenre", "view", "play", "mood", "bm")

# games 테이블 정의. C7-a2에서 tier가 규모(대형/중견/소형)로 바뀌고 인디 여부는 is_indie로 분리됨
GAMES_TABLE = """
CREATE TABLE IF NOT EXISTS {name} (
    id                 TEXT PRIMARY KEY,                -- games.json의 id (예: dq8), 자동 수집은 스토어 ID로 만듦
    name               TEXT NOT NULL,
    developer          TEXT,
    tier               TEXT CHECK (tier IN ('대형', '중견', '소형')),  -- 규모 (설치 수 기준 자동 분류)
    tier_locked        INTEGER NOT NULL DEFAULT 0 CHECK (tier_locked IN (0, 1)),  -- 1이면 자동 분류가 안 덮어씀
    is_indie           INTEGER CHECK (is_indie IN (0, 1)),               -- 인디 개발사 여부 (NULL = 미정)
    description        TEXT,
    thumbnail_url      TEXT,
    video_url          TEXT,
    android_id         TEXT,                            -- Google Play 패키지 이름
    ios_id             TEXT,                            -- App Store 앱 ID (숫자)
    android_url        TEXT,
    ios_url            TEXT,
    release_date       TEXT,                            -- YYYY-MM-DD
    store_rating       REAL,
    store_rating_count INTEGER,
    installs           INTEGER,                         -- Google Play 최소 설치 수 (예: 10000000 = "1,000만+")
    chart_rank_android INTEGER,                         -- 스토어 무료 인기 게임 차트 순위 (차트 밖이면 NULL)
    chart_rank_ios     INTEGER,
    source             TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'auto')),
    source_feed        TEXT NOT NULL DEFAULT 'manual' CHECK (source_feed IN ('manual', 'chart', 'new')),  -- 들어온 경로 (C7-f)
    review_status      TEXT NOT NULL DEFAULT 'ok' CHECK (review_status IN ('ok', 'pending', 'rejected')),
    tags_checked       INTEGER NOT NULL DEFAULT 0 CHECK (tags_checked IN (0, 1)),  -- 자동 수집 게임의 태그를 사람이 검수함 (C12-a)
    tag_source         TEXT NOT NULL DEFAULT 'auto' CHECK (tag_source IN ('auto', 'store', 'ai', 'human')),  -- 태그를 누가 붙였나 (C12-j)
    updated_at         TEXT                             -- 스토어 정보 마지막 갱신 (UTC, YYYY-MM-DD HH:MM:SS)
);"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,                       -- 임시 UUID
    nickname    TEXT,
    survey_done INTEGER NOT NULL DEFAULT 0 CHECK (survey_done IN (0, 1)),
    exp_group   TEXT CHECK (exp_group IN ('random', 'survey', 'feedback')),
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

{games_table}

CREATE TABLE IF NOT EXISTS tags (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('genre', 'subgenre', 'view', 'play', 'mood', 'bm')),
    UNIQUE (name, category)
);

CREATE TABLE IF NOT EXISTS game_tags (
    game_id TEXT    NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
    auto    INTEGER NOT NULL DEFAULT 0 CHECK (auto IN (0, 1)),  -- 1 = 스토어 장르·설명으로 자동 부여
    PRIMARY KEY (game_id, tag_id)
);

CREATE TABLE IF NOT EXISTS survey_answers (
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    question_id TEXT NOT NULL,
    answer      TEXT NOT NULL,                          -- 복수 선택은 JSON 배열 문자열
    PRIMARY KEY (user_id, question_id)
);

CREATE TABLE IF NOT EXISTS user_tag_scores (
    user_id TEXT    NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
    score   REAL    NOT NULL DEFAULT 0 CHECK (score BETWEEN -5 AND 5),
    PRIMARY KEY (user_id, tag_id)
);

CREATE TABLE IF NOT EXISTS reactions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    game_id    TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    type       TEXT NOT NULL CHECK (type IN ('like', 'store', 'dislike', 'view')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_reactions_user ON reactions (user_id, type, created_at);
CREATE INDEX IF NOT EXISTS idx_reactions_game ON reactions (game_id, type);

CREATE TABLE IF NOT EXISTS review_stats (
    game_id      TEXT    NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    star         INTEGER NOT NULL CHECK (star BETWEEN 1 AND 5),
    review_count INTEGER NOT NULL DEFAULT 0,            -- 별점 분포
    pos          INTEGER NOT NULL DEFAULT 0,            -- 문장 수
    neg          INTEGER NOT NULL DEFAULT 0,
    neu          INTEGER NOT NULL DEFAULT 0,
    irr          INTEGER NOT NULL DEFAULT 0,
    source       TEXT    NOT NULL CHECK (source IN ('temp', 'model')),
    PRIMARY KEY (game_id, star, source)
);

CREATE TABLE IF NOT EXISTS review_topics (
    game_id   TEXT    NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    topic     TEXT    NOT NULL CHECK (topic IN ('과금', '광고', '버그', '발열', '운영')),
    neg_count INTEGER NOT NULL DEFAULT 0,
    neg_ratio REAL    NOT NULL DEFAULT 0,
    source    TEXT    NOT NULL CHECK (source IN ('temp', 'model')),
    PRIMARY KEY (game_id, topic, source)
);

CREATE TABLE IF NOT EXISTS search_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword    TEXT NOT NULL,                           -- 사용자 ID는 저장하지 않음
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS comments (                   -- 상세 페이지 자유 댓글 (C8-c)
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id    TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,  -- 내 데이터 삭제 → 댓글도 삭제
    body       TEXT NOT NULL,                           -- 1~300자, 최대 10줄 (서버에서 검사)
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, -- UTC
    updated_at TEXT                                     -- 고치면 채움 (화면에 "(수정됨)")
);
CREATE INDEX IF NOT EXISTS idx_comments_game ON comments (game_id, created_at);

CREATE TABLE IF NOT EXISTS comment_votes (              -- 댓글 추천·비추천 (C9). 게임 추천 엔진에는 반영 안 함
    comment_id INTEGER NOT NULL REFERENCES comments(id) ON DELETE CASCADE,  -- 댓글 삭제 → 같이 삭제
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,        -- 내 데이터 삭제 → 같이 삭제
    value      INTEGER NOT NULL CHECK (value IN (1, -1)),                   -- 1 추천 / -1 비추천 (취소는 행 삭제)
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,                     -- UTC, 바꾸면 그 시각으로
    PRIMARY KEY (comment_id, user_id)                                       -- 한 사람이 댓글 하나에 하나만
);
CREATE INDEX IF NOT EXISTS idx_comment_votes_user ON comment_votes (user_id);

CREATE TABLE IF NOT EXISTS game_stats_daily (           -- 하루 1번 게임별 숫자 스냅샷 (인기 급상승 계산, C12-e)
    day                TEXT NOT NULL,                   -- YYYY-MM-DD (로컬 날짜)
    game_id            TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    installs           INTEGER,
    rating_count       INTEGER,
    chart_rank_android INTEGER,
    chart_rank_ios     INTEGER,
    PRIMARY KEY (day, game_id)
);

CREATE TABLE IF NOT EXISTS sync_runs (                  -- 스토어 자동 수집 실행 기록 (C7-a2)
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL CHECK (status IN ('running', 'ok', 'failed')),
    added       INTEGER NOT NULL DEFAULT 0,
    updated     INTEGER NOT NULL DEFAULT 0,
    pending     INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    note        TEXT
);
""".replace("{games_table}", GAMES_TABLE.format(name="games"))

# 나중에 추가한 컬럼: 이미 만들어진 DB에도 붙인다 (테이블, 컬럼, 타입)
ADDED_COLUMNS = (
    ("games", "installs", "INTEGER"),  # C7-a1
    # C7-a2
    ("games", "tier_locked", "INTEGER NOT NULL DEFAULT 0"),
    ("games", "is_indie", "INTEGER"),
    ("games", "android_id", "TEXT"),
    ("games", "ios_id", "TEXT"),
    ("games", "chart_rank_android", "INTEGER"),
    ("games", "chart_rank_ios", "INTEGER"),
    ("games", "source", "TEXT NOT NULL DEFAULT 'manual'"),
    ("games", "review_status", "TEXT NOT NULL DEFAULT 'ok'"),
    ("games", "updated_at", "TEXT"),
    ("game_tags", "auto", "INTEGER NOT NULL DEFAULT 0"),
    # C7-f
    ("games", "source_feed", "TEXT NOT NULL DEFAULT 'manual' CHECK (source_feed IN ('manual', 'chart', 'new'))"),
    # C12-a
    ("games", "tags_checked", "INTEGER NOT NULL DEFAULT 0 CHECK (tags_checked IN (0, 1))"),
    # C12-j
    ("games", "tag_source", "TEXT NOT NULL DEFAULT 'auto' CHECK (tag_source IN ('auto', 'store', 'ai', 'human'))"),
)

TABLES = (
    "users", "games", "tags", "game_tags", "survey_answers",
    "user_tag_scores", "reactions", "review_stats", "review_topics", "search_logs", "sync_runs", "comments",
    "comment_votes", "game_stats_daily",
)


def game_tag_pairs(game):
    """게임 하나의 (태그 이름, category) 목록."""
    pairs = [(game["genre"], "genre")]
    for category in LIST_CATEGORIES:
        pairs += [(name, category) for name in game.get(category, [])]
    return pairs


def load_games(conn, games):
    for game in games:
        # json에 없는 컬럼은 건드리지 않는다 (나중에 채운 값이 다시 실행해도 지워지지 않게).
        # 인디 여부: 처음 넣을 때만 json 값, 이후에는 DB 값(본인이 고친 값)을 그대로 둔다.
        # 규모(tier)는 json에 없음 → 스토어 자동 수집(sync_store.py)이 설치 수로 채움
        conn.execute(
            """
            INSERT INTO games (id, name, is_indie) VALUES (?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET name = excluded.name, is_indie = COALESCE(games.is_indie, excluded.is_indie)
            """,
            (game["id"], game["name"], 1 if game["indie"] else 0),
        )
        # 게임–태그 연결은 json 기준으로 다시 맞춘다 (json에서 빠진 태그는 연결 해제).
        conn.execute("DELETE FROM game_tags WHERE game_id = ?", (game["id"],))
        for name, category in game_tag_pairs(game):
            conn.execute("INSERT OR IGNORE INTO tags (name, category) VALUES (?, ?)", (name, category))
            conn.execute(
                """
                INSERT OR IGNORE INTO game_tags (game_id, tag_id)
                SELECT ?, id FROM tags WHERE name = ? AND category = ?
                """,
                (game["id"], name, category),
            )


def add_missing_columns(conn):
    for table, column, kind in ADDED_COLUMNS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if existing and column not in existing:  # 테이블이 아예 없으면 SCHEMA가 만든다
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    backfill_tag_source(conn)


def backfill_tag_source(conn):
    """C12-j: 직접 넣은 게임(source='manual', games.json)의 태그는 사람이 붙인 것 → tag_source='human'. 여러 번 돌려도 같음"""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(games)")}
    if "tag_source" in columns:
        conn.execute("UPDATE games SET tag_source = 'human' WHERE source = 'manual' AND tag_source = 'auto'")


def migrate_games_tier(conn) -> bool:
    """
    C7-a2: 예전 games.tier는 '인디'만 쓰던 칸(CHECK 대형/중견/인디) → 규모(대형/중견/소형)로 바꾸고
    인디 여부는 is_indie로 옮긴다. SQLite는 CHECK를 못 고치므로 공식 절차대로 테이블을 새로 만들어 옮긴다
    (외래 키 잠시 끔 → 새 테이블 → 복사 → 교체 → 외래 키 검사). 다른 테이블의 행(태그·반응 등)은 그대로.
    이미 바뀐 DB면 아무것도 안 하고 False.
    """
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'games'").fetchone()
    if row is None or "'인디'" not in row[0]:
        return False
    add_missing_columns(conn)
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")  # 트랜잭션 밖에서만 바뀜
    try:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(games)")]
        # 예전 tier 값은 '인디' 아니면 NULL뿐이었다 → 규모는 비우고(자동 분류가 채움) 인디는 is_indie로
        select = ", ".join("NULL" if c == "tier" else "CASE WHEN tier = '인디' THEN 1 ELSE is_indie END"
                           if c == "is_indie" else c for c in columns)
        conn.executescript(
            "BEGIN;"
            + GAMES_TABLE.format(name="games_new")
            + f"INSERT INTO games_new ({', '.join(columns)}) SELECT {select} FROM games;"
            "DROP TABLE games;"
            "ALTER TABLE games_new RENAME TO games;"
            "COMMIT;"
        )
        problems = conn.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise RuntimeError(f"외래 키 검사 실패: {problems[:5]}")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    return True


def backfill_store_ids(conn):
    """C7-a1에서 채운 스토어 링크에서 android_id·ios_id를 뽑아 둔다 (자동 수집의 같은 게임 판정용)."""
    for game_id, android_url, ios_url in conn.execute("SELECT id, android_url, ios_url FROM games").fetchall():
        android = re.search(r"[?&]id=([\w.]+)", android_url or "")
        ios = re.search(r"/id(\d+)", ios_url or "")
        conn.execute("UPDATE games SET android_id = COALESCE(android_id, ?), ios_id = COALESCE(ios_id, ?) WHERE id = ?",
                     (android.group(1) if android else None, ios.group(1) if ios else None, game_id))


def prepare(conn, games):
    """테이블 만들기 + 예전 DB 고치기 + games.json 적재 (테스트도 이걸 쓴다)."""
    conn.executescript(SCHEMA)
    add_missing_columns(conn)
    migrate_games_tier(conn)
    load_games(conn, games)
    backfill_tag_source(conn)  # 방금 넣은 games.json 게임
    backfill_store_ids(conn)
    # C7-f 이전 자동 수집은 모두 차트 경로
    conn.execute("UPDATE games SET source_feed = 'chart' WHERE source = 'auto' AND source_feed = 'manual'")


def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    games = json.loads(GAMES_JSON.read_text(encoding="utf-8"))

    with get_connection() as conn:
        prepare(conn, games)
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}

    print(f"DB: {DB_PATH}")
    for table, count in counts.items():
        print(f"  {table:<16} {count:>5}")


if __name__ == "__main__":
    main()

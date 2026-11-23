"""
추천 후보 전/후 표 (PROJECT.md 4.11 C11-e 비선호 연관 태그, C12-a 태그 검수 안 된 게임 빼기, C12-i 모자라면 채우기).

실제 DB를 임시 사본으로 복사해서(원본은 안 바꿈) 실제 사용자 + 시뮬 사용자마다
  - 추천 후보 수 (passes_filter 통과: 이미 본 게임·장르 필터·비선호·필수 태그)
  - 홈 슬라이드 장 수 (홈과 같은 /api/feed?section=match&limit=9&min_match=60, 기준 미달이면 대체 동작)
를 바꾸기 전 / 후로 계산해 마크다운 표로 출력한다. 후보가 MIN_CANDIDATES 미만인 사용자는 ⚠ 표시.
  --compare dislike (기본): 비선호 연관 태그 없이(전) / 있게(후)
  --compare checked       : 미검수 자동 수집 게임 포함(전) / 지금 규칙(후) — 맞춤 추천 후보 기준
  --compare fill          : 세 칸 — C12 전(미검수 포함) / C12 후(검수된 게임만) / C12-i 후(모자라면 미검수로 채움)

실행: python scripts/dislike_report.py [--compare checked|fill]
"""
import argparse
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path
from unittest import mock

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app import services  # noqa: E402
from app.config import DB_PATH  # noqa: E402
from app.reco import repository as repo  # noqa: E402
from app.reco.recommender import passes_filter  # noqa: E402

HOME_LIMIT, HOME_MIN_MATCH = 9, 60   # static/js/home.js와 같은 값
MIN_CANDIDATES = 9                   # 지시: 후보가 9개 미만이 되면 보고

SIMS = {
    "시뮬 A: RPG + 턴제RPG": {"genres": ["RPG"], "subgenres": ["턴제RPG"]},
    "시뮬 B: A + dq8·ff6": {"genres": ["RPG"], "subgenres": ["턴제RPG"], "favorites": ["dq8", "ff6"]},
    "시뮬 C: 퍼즐·캐주얼 + 힐링·귀여움": {"genres": ["퍼즐", "캐주얼"], "likes": {"mood": ["힐링", "귀여움"]}},
    "시뮬 E: RPG + 세부 2 + dq8 + 2D·애니풍 + 판타지 + 싱글": {
        "genres": ["RPG"], "subgenres": ["턴제RPG", "수집형RPG"], "favorites": ["dq8"],
        "likes": {"view": ["2D", "애니풍"], "mood": ["판타지"], "play": ["싱글"]}},
    "시뮬 F: RPG·액션 + 캐주얼·퍼즐 비선호": {
        "genres": ["RPG", "액션"], "dislikes": ["genre:캐주얼", "genre:퍼즐"]},
    "시뮬 G: 힐링·판타지(장르 안 고름) + 캐주얼·퍼즐 비선호": {
        "likes": {"mood": ["힐링", "판타지"]}, "dislikes": ["genre:캐주얼", "genre:퍼즐"]},
}


def measure(conn, user) -> tuple[int, int, bool]:
    catalog = services.Catalog(conn)
    profile = services.profile_for(conn, user, catalog)
    candidates = sum(1 for g in services.recommendation_pool(catalog, profile) if passes_filter(g, profile))  # 맞춤 추천 후보
    slides, fallback = services.match_feed(conn, user, catalog, HOME_LIMIT, HOME_MIN_MATCH)
    return candidates, len(slides), fallback


ALL_GAMES = lambda: mock.patch.object(services, "recommendation_pool", lambda catalog, profile: list(catalog.games))  # noqa: E731
NO_FILL = lambda: mock.patch.object(services, "unchecked_fill", lambda catalog, profile: [])  # noqa: E731  C12 그대로 (채우기 없음)
# 비교마다 앞 칸들의 바꾸기 (마지막 칸 = 지금 코드 그대로)
COLUMNS = {
    "dislike": [lambda: mock.patch.object(services, "expand_dislikes", lambda features: set(features))],
    "checked": [ALL_GAMES],
    "fill": [ALL_GAMES, NO_FILL],
}
BEFORE_PATCHES = COLUMNS  # 예전 이름


def report(db_path: Path = DB_PATH, compare: str = "dislike") -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "report.db"
        src, dst = sqlite3.connect(db_path), sqlite3.connect(copy)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        conn = sqlite3.connect(copy)
        conn.row_factory = sqlite3.Row
        try:
            users = [(f"실제 {i} ({u['exp_group']})", u["id"]) for i, u in enumerate(
                conn.execute("SELECT id, exp_group FROM users WHERE survey_done = 1 ORDER BY created_at"), 1)]
            for name, survey in SIMS.items():
                user_id = f"sim-{uuid.uuid4()}"
                conn.execute("INSERT INTO users (id, survey_done, exp_group) VALUES (?, 1, 'feedback')", (user_id,))
                repo.save_survey(conn, user_id, survey)
                repo.recompute_scores(conn, user_id)
                users.append((name, user_id))
            rows = []
            for name, user_id in users:
                user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
                columns = []
                for patch in COLUMNS[compare]:
                    with patch():
                        columns.append(measure(conn, user))
                columns.append(measure(conn, user))
                rows.append({"user": name, "columns": columns, "before": columns[0], "after": columns[-1]})
            return rows
        finally:
            conn.close()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="추천 후보 전/후 표")
    parser.add_argument("--compare", choices=sorted(COLUMNS), default="dislike")
    args = parser.parse_args()
    note = lambda n, f: f"{n}장" + (" (대체 동작)" if f else "")  # noqa: E731
    if args.compare == "fill":
        print("| 사용자 | 후보 C12 전 → C12 후 → C12-i 후 | 홈 슬라이드 C12 전 → C12 후 → C12-i 후 |")
    else:
        print("| 사용자 | 후보 전 → 후 | 홈 슬라이드 전 → 후 |")
    print("|---|---|---|")
    for row in report(compare=args.compare):
        last = row["columns"][-1][0]
        warn = " ⚠" if last < MIN_CANDIDATES else ""
        counts = " → ".join(str(c[0]) for c in row["columns"][:-1]) + f" → **{last}**{warn}"
        slides = " → ".join(note(c[1], c[2]) for c in row["columns"])
        print(f"| {row['user']} | {counts} | {slides} |")


if __name__ == "__main__":
    main()

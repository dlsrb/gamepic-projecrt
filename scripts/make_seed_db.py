"""
시작용 DB 만들기 (PROJECT.md 4.11 C10, docs/C10_배포_지시.md 1단계 3)).

지금 DB(data/gamepick.db)에서 게임·태그·스토어 정보만 남긴 사본을 data/seed.db로 만든다.
- 남김: games(검수 대기·제외 상태 그대로), tags, game_tags, review_stats, review_topics, sync_runs(마지막 수집 시각)
- 비움: users와 사용자에 딸린 것(설문 응답·태그 점수·반응·댓글·댓글 추천), search_logs
배포 서버가 처음 켜질 때 복원할 백업이 없으면 이 파일로 시작한다 (app/backup.py).

실행: python scripts/make_seed_db.py            (data/gamepick.db → data/seed.db)
      python scripts/make_seed_db.py --src A --out B
"""
import argparse
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.config import DB_PATH  # noqa: E402

SEED_PATH = BASE_DIR / "data" / "seed.db"
# 사용자 데이터 (users를 지우면 연쇄 삭제되지만, 빠짐없이 비었는지 확인하려고 하나씩 지운다)
USER_TABLES = ("comment_votes", "comments", "reactions", "user_tag_scores", "survey_answers", "users", "search_logs")
KEEP_TABLES = ("games", "tags", "game_tags", "review_stats", "review_topics", "sync_runs",
               "game_stats_daily")  # 스냅샷은 사용자 데이터가 아니라 남김 (C12-e: 다시 배포해도 급상승 기록이 이어지게)


def make_seed(src: Path, out: Path) -> dict[str, int]:
    """src의 일관된 사본(Connection.backup)을 out에 만들고 사용자 데이터를 비운다. 테이블별 행 수를 돌려준다."""
    if not src.exists():
        raise FileNotFoundError(f"DB가 없습니다: {src}")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    source, target = sqlite3.connect(src), sqlite3.connect(tmp)
    try:
        source.backup(target)  # 파일을 그대로 복사하지 않고 일관된 사본
    finally:
        source.close()
        target.close()
    conn = sqlite3.connect(tmp)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        for table in USER_TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN (%s)" % ", ".join("?" * len(USER_TABLES)), USER_TABLES)
        conn.commit()
        conn.execute("VACUUM")  # 지운 내용이 파일에 남지 않게
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in KEEP_TABLES + USER_TABLES}
        problems = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if problems != "ok":
            raise RuntimeError(f"무결성 검사 실패: {problems}")
    finally:
        conn.close()
    tmp.replace(out)
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description="게임·태그만 남긴 시작용 DB 만들기")
    parser.add_argument("--src", type=Path, default=DB_PATH)
    parser.add_argument("--out", type=Path, default=SEED_PATH)
    args = parser.parse_args(argv)
    counts = make_seed(args.src, args.out)
    print(f"{args.src} → {args.out} ({args.out.stat().st_size / 1024:.0f} KB)")
    for table, count in counts.items():
        print(f"  {table:<16} {count:>5}")


if __name__ == "__main__":
    main()

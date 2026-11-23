"""
C12-j 태그 출처 기록 (docs/C12-j_지시.md 1). games.tag_source: auto(자동 수집 규칙) / store(스토어 분류) / ai(AI 초안 승인) / human(사람)
실행: python -m unittest discover tests   (프로젝트 최상위에서)
"""
import csv
import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_connection  # noqa: E402
from scripts import init_db, tag_review  # noqa: E402
from tests.test_c12i import FillTestCase  # noqa: E402
from tests.test_sync_store import FakeStore, SyncTestCase  # noqa: E402

GAMES = json.loads(init_db.GAMES_JSON.read_text(encoding="utf-8"))


class TagSourceSchemaTest(unittest.TestCase):
    def test_new_db_manual_games_are_human(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_db.prepare(conn, GAMES)
        counts = dict(conn.execute("SELECT tag_source, COUNT(*) FROM games GROUP BY tag_source").fetchall())
        self.assertEqual(counts, {"human": len(GAMES)})

    def test_old_db_gets_column_and_backfill(self):
        """C12-j 전 DB: tag_source 칸이 없음 → 서버 시작 때(add_missing_columns) 칸 추가 + 직접 넣은 게임은 human"""
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        old_schema = init_db.SCHEMA.replace(
            "    tag_source         TEXT NOT NULL DEFAULT 'auto' CHECK (tag_source IN ('auto', 'store', 'ai', 'human')),"
            "  -- 태그를 누가 붙였나 (C12-j)\n", "")
        self.assertNotEqual(old_schema, init_db.SCHEMA)
        conn.executescript(old_schema)
        init_db.load_games(conn, GAMES)
        conn.execute("INSERT INTO games (id, name, source) VALUES ('auto1', '자동', 'auto')")
        init_db.add_missing_columns(conn)
        rows = dict(conn.execute("SELECT id, tag_source FROM games WHERE id IN ('dq8', 'auto1')").fetchall())
        self.assertEqual(rows, {"dq8": "human", "auto1": "auto"})
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE games SET tag_source = 'robot' WHERE id = 'auto1'")


class NewAutoGameSourceTest(SyncTestCase):
    def test_sync_new_games_are_auto(self):
        self.run_sync(FakeStore())
        with get_connection(self.db_path) as conn:
            rows = conn.execute("SELECT source, tag_source FROM games WHERE source = 'auto'").fetchall()
            self.assertTrue(rows)
            self.assertEqual({r["tag_source"] for r in rows}, {"auto"})


class ImportSourceTest(FillTestCase):
    def setUp(self):
        super().setUp()
        conn = self.db()
        conn.execute("UPDATE games SET source = 'auto', tags_checked = 0, tag_source = 'auto' "
                     "WHERE id IN ('genshin', 'odin', 'epic7', 'hsr')")
        conn.execute("UPDATE games SET tag_source = 'human' WHERE source = 'manual'")
        conn.commit()
        self.csv = Path(self.tmp.name) / "review.csv"

    def write_ai_drafts(self):
        with get_connection(self.db_path) as conn:
            tag_review.export(conn, self.csv)
        lines = self.csv.read_text(encoding="utf-8-sig").splitlines()
        rows = list(csv.DictReader(lines[1:]))
        for row in rows:  # AI 초안: 확신 높음·중간 = checked 1, 낮음 = 빈칸 (지시와 같은 모양, 열 2개 더 있음)
            row["확신"] = "낮음" if row["id"] == "hsr" else "높음"
            row["checked"] = "" if row["id"] == "hsr" else "1"
            row["suggested_tags"] = "RPG, MMORPG, 판타지" if row["id"] == "odin" else ""
        with self.csv.open("w", encoding="utf-8-sig", newline="") as f:
            f.write("# AI 초안\r\n")
            writer = csv.DictWriter(f, fieldnames=[*tag_review.FIELDS, "확신", "메모"])
            writer.writeheader()
            writer.writerows([{**r, "메모": ""} for r in rows])

    def test_ai_import_sets_source_and_counts(self):
        self.write_ai_drafts()
        with get_connection(self.db_path) as conn:
            report = tag_review.import_csv(conn, self.csv, source="ai")
            self.assertEqual((report["changed"], sorted(report["checked_only"])), (["odin"], ["epic7", "genshin"]))
            counts = {name: (total, checked) for name, total, checked in tag_review.source_counts(conn)}
        rows = dict(self.db().execute("SELECT id, tag_source FROM games WHERE id IN ('genshin', 'odin', 'epic7', 'hsr')"))
        self.assertEqual(rows, {"genshin": "ai", "odin": "ai", "epic7": "ai", "hsr": "auto"})   # 낮음은 미검수 그대로
        self.assertEqual(counts["ai"], (3, 3))
        self.assertEqual(counts["auto"], (1, 0))
        self.assertEqual(counts["store"], (0, 0))
        self.assertEqual(counts["human"][0], counts["human"][1])          # human은 모두 검수로 침
        # ai 게임도 checked → 맞춤 추천 후보 (C12-a 의미 그대로)
        self.assertEqual(self.db().execute("SELECT tags_checked FROM games WHERE id = 'genshin'").fetchone()[0], 1)

    def test_dry_run_changes_nothing(self):
        self.write_ai_drafts()
        with get_connection(self.db_path) as conn:
            tag_review.import_csv(conn, self.csv, dry_run=True, source="ai")
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM games WHERE tag_source = 'ai'").fetchone()[0], 0)

    def test_add_is_human_and_bad_source(self):
        with get_connection(self.db_path) as conn:
            tag_review.add_tags(conn, "hsr", ["귀여움"])
            with self.assertRaises(tag_review.TagError):
                tag_review.mark_checked(conn, "odin", "robot")
        self.assertEqual(self.db().execute("SELECT tag_source FROM games WHERE id = 'hsr'").fetchone()[0], "human")

    def test_cli_sources_table(self):
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        out = io.StringIO()
        with mock.patch.object(tag_review, "DB_PATH", self.db_path), redirect_stdout(out):
            tag_review.main(["--sources"])
        text = out.getvalue()
        self.assertIn("| 출처 | 게임 |", text)
        self.assertIn("| auto | 4 | 0 |", text)


if __name__ == "__main__":
    unittest.main()

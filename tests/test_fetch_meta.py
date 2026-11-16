"""
게임 정보 채우기 테스트 (PROJECT.md 4.11 C7-a1). 스토어에는 접속하지 않고 응답을 흉내 낸다.
실행: python -m unittest discover tests   (프로젝트 최상위에서)
"""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import fetch_game_meta as fetch  # noqa: E402
from scripts import init_db  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20
PLAY_INFO = {"title": "브롤스타즈", "icon": "https://play-lh.example/icon", "summary": "3:3 대전", "developer": "Supercell",
             "minInstalls": 500000000, "installs": "500,000,000+", "score": 4.123, "ratings": 1234}


def fake_get(url, params=None, timeout=None):
    response = mock.Mock()
    response.raise_for_status = mock.Mock()
    if params:  # App Store 조회
        response.json.return_value = {"results": [{"trackName": "브롤스타즈"}]} if params["id"] == "1229016807" else {"results": []}
    else:       # 아이콘
        response.content = PNG
    return response


class FetchMetaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch = mock.patch.object(fetch, "ICON_DIR", Path(self.tmp.name) / "games")
        patch.start()
        self.addCleanup(patch.stop)
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript(init_db.SCHEMA)
        init_db.load_games(self.conn, json.loads(init_db.GAMES_JSON.read_text(encoding="utf-8")))

    def run_one(self, ios_id):
        row = {"android_id": "com.supercell.brawlstars", "ios_id": ios_id}
        with mock.patch.object(fetch, "play_app", return_value=PLAY_INFO), \
                mock.patch.object(fetch.requests, "get", side_effect=fake_get):
            line = fetch.fetch_one(self.conn, "brawl", "브롤스타즈", row)
        return line, self.conn.execute("SELECT * FROM games WHERE id = 'brawl'").fetchone()

    def test_fills_game_and_saves_icon(self):
        line, game = self.run_one("1229016807")
        self.assertEqual(game["thumbnail_url"], "/static/img/games/brawl.png")
        self.assertEqual((Path(self.tmp.name) / "games" / "brawl.png").read_bytes(), PNG)
        self.assertEqual((game["developer"], game["description"], game["installs"]), ("Supercell", "3:3 대전", 500000000))
        self.assertEqual((game["store_rating"], game["store_rating_count"]), (4.12, 1234))
        self.assertEqual(game["android_url"], "https://play.google.com/store/apps/details?id=com.supercell.brawlstars")
        self.assertEqual(game["ios_url"], "https://apps.apple.com/kr/app/id1229016807")
        self.assertIn("Android: 브롤스타즈", line)
        self.assertIn("iOS: 브롤스타즈", line)
        self.assertIsNone(game["tier"])  # 규모는 건드리지 않음 (games.json에서 인디가 아니라 비어 있던 값 그대로)

    def test_unknown_ios_id_leaves_link_empty(self):
        line, game = self.run_one("999")
        self.assertIsNone(game["ios_url"])
        self.assertIn("App Store에 없음", line)

    def test_rerun_is_safe(self):
        self.run_one("1229016807")
        _, game = self.run_one("1229016807")
        self.assertEqual(game["installs"], 500000000)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM games").fetchone()[0], 36)

    def test_non_image_icon_rejected(self):
        bad = mock.Mock(content=b"<html>", raise_for_status=mock.Mock())
        with mock.patch.object(fetch.requests, "get", return_value=bad):
            with self.assertRaises(ValueError):
                fetch.download_icon("https://x", "brawl")

    def test_name_warning(self):
        self.assertGreaterEqual(fetch.similarity("던전앤파이터 모바일", "던전앤파이터 모바일(12)"), fetch.NAME_MATCH_WARN)
        self.assertLess(fetch.similarity("마인크래프트", "플래닛크래프트: 마인크래프트 건설 서바이벌 온라인게임"), fetch.NAME_MATCH_WARN)
        self.assertLess(fetch.similarity("슬레이 더 스파이어", "슬레이 더 타이탄 : 덱빌딩 RPG"), fetch.NAME_MATCH_WARN)


class AddColumnTest(unittest.TestCase):
    def test_old_db_gets_installs_column(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE games (id TEXT PRIMARY KEY, name TEXT NOT NULL)")  # 컬럼 추가 전 모양
        init_db.add_missing_columns(conn)
        init_db.add_missing_columns(conn)  # 두 번 해도 안전
        self.assertIn("installs", [row[1] for row in conn.execute("PRAGMA table_info(games)")])


if __name__ == "__main__":
    unittest.main()

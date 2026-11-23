"""
C13 게임픽 MCP 서버 (docs/C13_MCP_지시.md). 도구 4개를 보통 함수로 직접 부른다 (MCP 연결 자체는 mcp_server/smoke_test.py).
mcp 패키지(requirements-mcp.txt)가 없으면 서버 등록 테스트만 건너뜀.
실행: python -m unittest discover tests   (프로젝트 최상위에서)
"""
import asyncio
import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server import tools  # noqa: E402
from tests.test_api import ApiTestCase  # noqa: E402

try:
    import mcp  # noqa: F401
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

BRIEF_KEYS = {"id", "name", "genre", "tags", "tag_source", "tags_checked", "badges", "store_links"}
CUTE = {"maplem", "bluearchive", "7kidle", "mushroom", "cookiekingdom", "soulknight", "brawl", "clashroyale", "coc",
        "candy", "anipang4", "royalmatch", "cookierun", "amongus", "pokemongo"}


class McpToolsTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        conn = self.db()
        conn.execute("UPDATE games SET android_url = 'https://play.google.com/store/apps/details?id=com.dq8' WHERE id = 'dq8'")
        conn.execute("UPDATE games SET chart_rank_android = 1 WHERE id = 'brawl'")
        conn.execute("UPDATE games SET chart_rank_ios = 2 WHERE id = 'dq8'")
        conn.commit()
        self.ro = tools.open_readonly(self.db_path)
        self.addCleanup(self.ro.close)

    # ---------- DB ----------
    def test_readonly_connection(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.ro.execute("INSERT INTO search_logs (keyword) VALUES ('x')")
        with self.assertRaises(sqlite3.OperationalError):
            self.ro.execute("UPDATE games SET name = 'x'")
        with self.assertRaises(FileNotFoundError):
            tools.open_readonly(Path(self.tmp.name) / "none.db")

    # ---------- 1. 검색 ----------
    def test_search_games(self):
        result = tools.search_games(self.ro, "턴제RPG", limit=3)
        self.assertEqual(result["interpreted_as"][0]["kind"], "tag")
        self.assertEqual(len(result["games"]), 3)
        self.assertTrue(BRIEF_KEYS <= set(result["games"][0]))
        dq8 = tools.search_games(self.ro, "드래곤 퀘스트")["games"][0]
        self.assertEqual(dq8["id"], "dq8")
        self.assertEqual(dq8["store_links"]["google_play"], "https://play.google.com/store/apps/details?id=com.dq8")
        self.assertEqual(tools.search_games(self.ro, "인기")["interpreted_as"][0]["kind"], "badge")
        self.assertIn("error", tools.search_games(self.ro, "  "))

    def test_search_logs_nothing(self):
        """MCP 검색은 search_logs에 안 남김 (읽기 전용)"""
        before = self.db().execute("SELECT COUNT(*) FROM search_logs").fetchone()[0]
        tools.search_games(self.ro, "RPG")
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM search_logs").fetchone()[0], before)

    # ---------- 2. 상세 ----------
    def test_get_game(self):
        game = tools.get_game(self.ro, "dq8")
        self.assertTrue(BRIEF_KEYS <= set(game))
        for key in ("description", "release_date", "store_rating", "installs", "chart_rank", "similar_games"):
            self.assertIn(key, game)
        self.assertEqual(len(game["similar_games"]), tools.SIMILAR_COUNT)
        self.assertNotIn("dq8", [g["id"] for g in game["similar_games"]])

    def test_get_game_unknown_id(self):
        result = tools.get_game(self.ro, "dq")
        self.assertIn("없는 게임 id", result["error"])
        self.assertIn("dq8", [g["id"] for g in result["maybe"]])

    # ---------- 3. 추천 ----------
    def test_recommend_excludes_dislikes_with_related(self):
        result = tools.recommend_games(self.ro, ["RPG"], ["캐주얼"], limit=30)
        ids = {g["id"] for g in result["games"]}
        self.assertTrue(ids)
        self.assertFalse(ids & CUTE)                                             # 캐주얼 → 귀여움 (연관 태그)
        self.assertIn("귀여움", result["profile"]["excluded_with_related"])
        self.assertTrue(all(g["genre"] == "RPG" for g in result["games"]))       # 장르를 좋아하면 그 장르만
        first = result["games"][0]
        self.assertTrue({"match", "reasons"} <= set(first))

    def test_recommend_checked_first(self):
        conn = self.db()
        conn.execute("UPDATE games SET source = 'auto', tags_checked = 0 WHERE id IN ('hsr', 'epic7', 'summoners', 'darkest', "
                     "'genshin', 'odin', 'lineagem', 'dnfm', 'chrono')")
        conn.commit()
        games = tools.recommend_games(self.ro, ["턴제RPG"], limit=10)["games"]
        checked = [g["tags_checked"] for g in games]
        self.assertEqual(checked, sorted(checked, reverse=True))                # 검수된 게임 먼저, 채운 미검수는 뒤
        self.assertTrue(any(not c for c in checked))                           # 모자라서 미검수로 채움 (C12-i)

    def test_recommend_favorites(self):
        result = tools.recommend_games(self.ro, [], favorite_game_ids=["stardew"], limit=5)
        self.assertNotIn("stardew", [g["id"] for g in result["games"]])         # 좋아하는 게임 자신은 뺌
        self.assertEqual(len(result["games"]), 5)

    def test_recommend_bad_input_guides(self):
        bad = tools.recommend_games(self.ro, ["퍼즐", "없는태그"])
        self.assertIn("없는 태그: 없는태그", bad["error"])
        self.assertIn("퍼즐", bad["available_tags"]["장르"])
        self.assertIn("error", tools.recommend_games(self.ro, []))
        self.assertIn("없는 게임 id", tools.recommend_games(self.ro, ["RPG"], favorite_game_ids=["nope"])["error"])

    # ---------- 4. 랭킹 ----------
    def test_rankings(self):
        popular = tools.get_rankings(self.ro, "popular", "전체", 5)
        self.assertEqual([g["id"] for g in popular["games"]][:2], ["brawl", "dq8"])
        self.assertEqual(popular["games"][0]["rank"], 1)
        self.assertEqual(tools.get_rankings(self.ro, "popular", "Google Play")["games"][0]["id"], "brawl")
        self.assertEqual(tools.get_rankings(self.ro, "popular", "play")["games"][0]["id"], "brawl")   # 키도 됨
        self.assertIn("tabs", tools.get_rankings(self.ro, "popular", "없는탭"))
        self.assertIn("kinds", tools.get_rankings(self.ro, "nope"))
        self.assertIn("games", tools.get_rankings(self.ro, "monthly"))
        surge = tools.get_rankings(self.ro, "surge")          # C15-a부터: 기록 부족 안내 + 인기 랭킹으로 대신
        self.assertEqual(surge["substituted"], "popular")
        self.assertIn("급상승은 기록이 7일 쌓이면 보여요", surge["note"])

    def test_likes_ranking_and_no_personal_data(self):
        """추천 순위는 모아 센 수만. 사용자 id·닉네임·댓글 본문·반응 기록은 어떤 도구 결과에도 없음"""
        self.client.post("/api/users")
        self.assertEqual(self.client.patch("/api/me", json={"nickname": "비밀닉네임"}).status_code, 200)
        self.assertEqual(self.client.post("/api/reactions", json={"game_id": "dq8", "type": "like"}).status_code, 200)
        self.assertEqual(self.client.post("/api/games/dq8/comments", json={"body": "비밀 댓글 본문"}).status_code, 201)
        user_id = self.uid()
        self.assertEqual(self.db().execute("SELECT nickname FROM users WHERE id = ?", (user_id,)).fetchone()[0], "비밀닉네임")
        likes = tools.get_rankings(self.ro, "likes")
        self.assertEqual((likes["games"][0]["id"], likes["games"][0]["like_count"]), ("dq8", 1))
        outputs = [likes, tools.get_game(self.ro, "dq8"), tools.search_games(self.ro, "드래곤"),
                   tools.recommend_games(self.ro, ["RPG"]), tools.get_rankings(self.ro, "popular"),
                   tools.get_rankings(self.ro, "monthly")]
        text = json.dumps(outputs, ensure_ascii=False)
        for secret in (user_id, "비밀닉네임", "비밀 댓글 본문"):
            self.assertNotIn(secret, text)
        for key in ('"user_id"', '"nickname"', '"comments"', '"reactions"'):
            self.assertNotIn(key, text)

    def test_only_visible_games(self):
        self.db().execute("UPDATE games SET review_status = 'pending' WHERE id = 'gris'").connection.commit()
        self.assertNotIn("gris", [g["id"] for g in tools.search_games(self.ro, "GRIS")["games"]])
        self.assertIn("error", tools.get_game(self.ro, "gris"))


@unittest.skipUnless(HAS_MCP, "mcp 패키지가 없어 서버 등록 확인을 건너뜀 (pip install -r requirements-mcp.txt)")
class McpServerTest(unittest.TestCase):
    def test_four_read_only_tools(self):
        from mcp_server import gamepick_mcp
        listed = asyncio.run(gamepick_mcp.mcp.list_tools())
        self.assertEqual({t.name for t in listed},
                         {"search_games", "get_game", "recommend_games", "get_rankings"})
        for tool in listed:
            self.assertTrue(tool.annotations.read_only_hint, tool.name)
            self.assertTrue(tool.description)


class PackExcludesMcpTest(unittest.TestCase):
    def test_zip_skips_mcp(self):
        from scripts import pack_for_replit
        self.assertIn("mcp_server/", pack_for_replit.ALWAYS_SKIP)
        self.assertIn("requirements-mcp.txt", pack_for_replit.ALWAYS_SKIP)
        requirements = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text(encoding="utf-8")
        self.assertNotIn("mcp==", requirements)   # 배포용 의존성에 mcp 없음


if __name__ == "__main__":
    unittest.main()

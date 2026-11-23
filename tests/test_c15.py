"""
C15 시뮬레이션에서 나온 작은 수정 3가지 (docs/C15_지시.md, docs/sim_notes.md 2·3·4).
실행: python -m unittest discover tests   (프로젝트 최상위에서)
"""
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import rankings, services  # noqa: E402
from app.reco import reco_config as cfg  # noqa: E402
from app.reco.recommender import UserProfile  # noqa: E402
from mcp_server import tools  # noqa: E402
from tests.test_api import ApiTestCase  # noqa: E402

TODAY = date.today()
CASUAL_HATER = {"genres": ["RPG", "액션"], "dislikes": ["genre:캐주얼"]}   # 캐주얼 → 귀여움(연관 태그)도


class C15Case(ApiTestCase):
    def charts(self, ranks: dict[str, int]):
        conn = self.db()
        conn.execute("UPDATE games SET chart_rank_android = NULL, chart_rank_ios = NULL")
        for game_id, rank in ranks.items():
            conn.execute("UPDATE games SET chart_rank_android = ? WHERE id = ?", (rank, game_id))
        conn.commit()

    def snapshot(self, days_ago: int, game_id: str, rating_count: int, android: int | None):
        self.db().execute("INSERT OR REPLACE INTO game_stats_daily (day, game_id, rating_count, chart_rank_android) "
                          "VALUES (?, ?, ?, ?)", ((TODAY - timedelta(days=days_ago)).isoformat(), game_id,
                                                  rating_count, android)).connection.commit()

    def search(self, q):
        return self.client.get("/api/search", params={"q": q}).json()


# ---------- C15-a 급상승 기록 부족 안내 ----------
class SurgeNoticeTest(C15Case):
    def setUp(self):
        super().setUp()
        self.charts({"brawl": 3, "dq8": 1})
        self.db().execute("UPDATE games SET store_rating_count = 200 WHERE id = 'brawl'").connection.commit()

    def test_under_7_days_notice_and_popular_instead(self):
        self.snapshot(2, "brawl", 100, 50)                                   # 기록 3일째
        body = self.search("급상승")
        self.assertEqual(body["notice"], "급상승은 기록이 7일 쌓이면 보여요 (지금 3일). 대신 인기 게임을 보여 드려요")
        self.assertEqual({g["id"] for g in body["games"]}, {"brawl", "dq8"})  # 인기(차트 30위 안) 게임으로 대신
        self.assertEqual(body["keywords"][0]["label"], "인기 (급상승 대신)")
        self.assertEqual(self.client.get("/api/popular").json()["surge_notice"],
                         "급상승은 기록이 7일 쌓이면 보여요 (지금 3일).")
        self.assertIsNone(self.search("RPG")["notice"])                    # 급상승이 아닌 검색엔 안내 없음

    def test_no_snapshot_says_0_days(self):
        self.assertIn("(지금 0일)", self.search("급상승")["notice"])

    def test_after_7_days_normal(self):
        self.snapshot(8, "brawl", 100, 50)                                   # 7일 넘음 → 급상승 계산
        self.snapshot(8, "dq8", 1000, 1)
        body = self.search("급상승")
        self.assertIsNone(body["notice"])
        self.assertEqual([g["id"] for g in body["games"]], ["brawl"])        # 진짜 급상승 게임
        self.assertIsNone(self.client.get("/api/popular").json()["surge_notice"])

    def test_mcp_same_notice(self):
        ro = tools.open_readonly(self.db_path)
        self.addCleanup(ro.close)
        search = tools.search_games(ro, "급상승")
        self.assertIn("급상승은 기록이 7일 쌓이면 보여요", search["note"])
        surge = tools.get_rankings(ro, "surge")
        self.assertEqual(surge["note"], search["note"])
        self.assertEqual(surge["substituted"], "popular")
        self.assertNotIn("note", tools.search_games(ro, "RPG"))


# ---------- C15-b 일부만 맞는 게임 ----------
class PartialSearchTest(C15Case):
    def test_and_first(self):
        body = self.search("턴제RPG 판타지")
        self.assertFalse(body["partial"])
        self.assertTrue(body["games"])
        self.assertNotIn("matched_words", body["games"][0])

    def test_partial_only_when_and_is_empty(self):
        body = self.search("힐링 PvP")                                       # 둘 다인 게임 없음
        self.assertTrue(body["partial"])
        counts = [len(g["matched_words"]) for g in body["games"]]
        self.assertEqual(counts, sorted(counts, reverse=True))               # 맞은 단어 수 많은 순
        self.assertTrue(all(set(g["matched_words"]) <= {"힐링", "PvP"} for g in body["games"]))
        ids = {g["id"] for g in body["games"]}
        self.assertIn("monument", ids)                                       # 힐링만 맞음
        self.assertIn("lineagem", ids)                                       # PvP만 맞음

    def test_one_and_hit_means_no_partial(self):
        conn = self.db()   # 리니지M(PvP)에 힐링 태그를 붙이면 둘 다 맞는 게임이 1개 → 일부 일치로 안 넘어감
        conn.execute("INSERT INTO game_tags (game_id, tag_id) SELECT 'lineagem', id FROM tags WHERE name = '힐링'")
        conn.commit()
        body = self.search("힐링 PvP")
        self.assertFalse(body["partial"])
        self.assertEqual([g["id"] for g in body["games"]], ["lineagem"])

    def test_single_word_and_empty_stays_empty(self):
        body = self.search("없는단어")
        self.assertEqual((body["games"], body["partial"]), ([], False))

    def test_partial_keeps_dislike_rule(self):
        self.survey(CASUAL_HATER)
        body = self.search("소울 MMORPG")                                    # AND 0개 → 일부 일치
        self.assertTrue(body["partial"])
        games = {g["id"]: g for g in body["games"]}
        self.assertIn("soulknight", games)                                   # 이름 일부 → 비선호(귀여움)여도 남음
        self.assertTrue(games["soulknight"]["disliked"])
        self.assertEqual(games["soulknight"]["matched_words"], ["소울"])
        self.assertNotIn("maplem", games)                                    # MMORPG(태그)로만 맞고 귀여움 → 뺌
        self.assertIn("lineagem", games)

    def test_mcp_partial(self):
        ro = tools.open_readonly(self.db_path)
        self.addCleanup(ro.close)
        result = tools.search_games(ro, "힐링 PvP", limit=30)
        self.assertTrue(result["partial"])
        monument = next(g for g in result["games"] if g["id"] == "monument")
        self.assertEqual((monument["matched_words"], monument["missed_words"]), (["힐링"], ["PvP"]))
        self.assertFalse(tools.search_games(ro, "턴제RPG")["partial"])


# ---------- C15-c 이달의 게임에 따봉 ----------
class MonthlyLikesTest(C15Case):
    def setUp(self):
        super().setUp()
        # 홈 인기 5개(빠짐) + 차트 6위 1개 → 후보는 'odin' + 따봉 게임
        self.charts({"brawl": 1, "candy": 2, "dq8": 3, "ff6": 4, "chrono": 5, "odin": 6})

    def like(self, game_id: str, when: str | None = None):
        uid = f"u-{game_id}-{when or 'now'}-{self.db().execute('SELECT COUNT(*) FROM reactions').fetchone()[0]}"
        conn = self.db()
        conn.execute("INSERT INTO users (id) VALUES (?)", (uid,))
        if when:
            conn.execute("INSERT INTO reactions (user_id, game_id, type, created_at) VALUES (?, ?, 'like', ?)",
                         (uid, game_id, when))
        else:
            conn.execute("INSERT INTO reactions (user_id, game_id, type) VALUES (?, ?, 'like')", (uid, game_id))
        conn.commit()

    def monthly_ids(self):
        catalog = services.Catalog(self.db())
        return [p["game"]["id"] for p in rankings.monthly(catalog, UserProfile())]

    def test_weights(self):
        self.assertEqual(cfg.MONTHLY_WEIGHTS, {"popular": 0.55, "surge": 0.25, "likes": 0.1, "match": 0.1})

    def test_this_month_likes_join_and_rank(self):
        self.assertEqual(self.monthly_ids(), ["odin"])
        for _ in range(3):
            self.like("stardew")
        self.like("gris")
        ids = self.monthly_ids()
        self.assertEqual(ids[0], "odin")                                     # 차트 인기가 여전히 먼저 (따봉은 약하게)
        self.assertEqual(ids[1:], ["stardew", "gris"])                       # 따봉 많은 순
        picked = {p["game"]["id"]: p for p in rankings.monthly(services.Catalog(self.db()), UserProfile())}
        self.assertEqual(picked["stardew"]["month_likes"], 3)
        self.assertAlmostEqual(picked["stardew"]["score"], 0.1)              # 따봉 최댓값 → 0.1 × 1
        self.assertAlmostEqual(picked["gris"]["score"], round(0.1 / 3, 4))

    def test_last_month_likes_ignored(self):
        last_month = (TODAY.replace(day=1) - timedelta(days=2)).isoformat() + " 12:00:00"
        self.like("stardew", last_month)
        self.assertEqual(self.monthly_ids(), ["odin"])

    def test_dislikes_and_home_top5_still_excluded(self):
        self.like("cookiekingdom")                                          # 귀여움
        self.like("brawl")                                                  # 홈 인기 5개 안
        self.survey(CASUAL_HATER)
        ids = [g["id"] for g in self.client.get("/api/monthly").json()["games"]]
        self.assertNotIn("cookiekingdom", ids)
        self.assertNotIn("brawl", ids)


if __name__ == "__main__":
    unittest.main()

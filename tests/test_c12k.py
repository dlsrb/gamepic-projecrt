"""
C12-k 배포 확인 뒤 고친 것 (docs/C12-k_지시.md): 스토어 링크 점검, 검색 이름 부분 일치, 랭킹 탭 채우기.
실행: python -m unittest discover tests   (프로젝트 최상위에서)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_connection  # noqa: E402
from app.reco import reco_config as cfg  # noqa: E402
from scripts import check_store_links  # noqa: E402
from tests.test_api import ApiTestCase  # noqa: E402

CASUAL_HATER = {"genres": ["RPG", "액션"], "dislikes": ["genre:캐주얼"]}   # 캐주얼 → 귀여움(연관 태그)도 비선호


# ---------- 2. 검색 ----------
class SearchNameTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.survey(CASUAL_HATER)

    def search(self, **params):
        return self.client.get("/api/search", params=params).json()["games"]

    def test_name_part_keeps_disliked_with_note(self):
        soul = self.search(q="소울")
        self.assertEqual([g["id"] for g in soul], ["soulknight"])       # 소울 나이트 = 액션 + 귀여움 (비선호)
        self.assertTrue(soul[0]["disliked"])
        maple = self.search(q="메이플")
        self.assertIn("maplem", [g["id"] for g in maple])
        self.assertTrue(next(g for g in maple if g["id"] == "maplem")["disliked"])
        self.assertEqual([g["id"] for g in self.search(q="소울나이트")], ["soulknight"])  # 띄어쓰기 무시

    def test_tag_search_still_excludes_dislikes(self):
        rpg = [g["id"] for g in self.search(tags="RPG")]
        self.assertTrue(rpg)
        self.assertFalse(set(rpg) & {"maplem", "bluearchive", "7kidle", "mushroom", "cookiekingdom"})
        self.assertNotIn("disliked", self.search(tags="RPG")[0])
        self.assertNotIn("soulknight", [g["id"] for g in self.search(q="액션")])     # 장르 태그로 찾으면 빠짐

    def test_not_disliked_has_no_note(self):
        dq8 = self.search(q="드래곤 퀘스트")
        self.assertEqual(dq8[0]["id"], "dq8")
        self.assertNotIn("disliked", dq8[0])

    def test_order_exact_prefix_part(self):
        conn = self.db()
        conn.execute("UPDATE games SET name = '킹덤' WHERE id = 'odin'")
        conn.execute("UPDATE games SET name = '킹덤 러너' WHERE id = 'lineagem'")
        conn.execute("UPDATE games SET name = '블랙 킹덤' WHERE id = 'epic7'")
        conn.commit()
        ids = [g["id"] for g in self.search(q="킹덤")]
        self.assertEqual(ids[:2], ["odin", "lineagem"])                  # 정확히 → 앞부분
        self.assertLess(ids.index("lineagem"), ids.index("epic7"))      # 앞부분 → 부분
        self.assertIn("cookiekingdom", ids)                              # 쿠키런: 킹덤 (부분, 비선호지만 이름으로 남음)
        self.assertTrue(next(g for g in self.search(q="킹덤") if g["id"] == "cookiekingdom")["disliked"])

    def test_suggest_same_name_rule(self):
        suggest = lambda q: [s["text"] for s in self.client.get("/api/search/suggest", params={"q": q}).json()["suggestions"]  # noqa: E731
                             if s["type"] == "game"]
        self.assertEqual(suggest("소울나"), ["소울 나이트"])              # 띄어쓰기 무시
        self.assertIn("소울 나이트", suggest("나이트"))                   # 부분 일치도
        self.assertIn("메이플스토리M", suggest("메이플"))                  # 비선호여도 (이름으로 찾음)


# ---------- 3. 랭킹 ----------
class RankingFillTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        conn = self.db()
        conn.execute("UPDATE games SET chart_rank_android = NULL, chart_rank_ios = NULL, installs = NULL, "
                     "store_rating_count = NULL")
        conn.execute("UPDATE games SET chart_rank_android = 1 WHERE id = 'brawl'")       # 액션 차트 1개
        conn.execute("UPDATE games SET chart_rank_ios = 2 WHERE id = 'candy'")            # 퍼즐(귀여움) 차트
        for game_id, installs, ratings in (("soulknight", 10_000_000, 500_000), ("deadcells", 1_000_000, 50_000),
                                           ("dnfm", None, 900_000)):
            conn.execute("UPDATE games SET installs = ?, store_rating_count = ? WHERE id = ?", (installs, ratings, game_id))
        conn.commit()

    def tab(self, tab, **params):
        return self.client.get("/api/popular", params={"tab": tab, **params}).json()["games"]

    def test_genre_tab_fills_after_chart(self):
        action = self.tab("액션")
        n_action = self.db().execute("SELECT COUNT(*) FROM game_tags gt JOIN tags t ON t.id = gt.tag_id "
                                     "WHERE t.category = 'genre' AND t.name = '액션'").fetchone()[0]
        self.assertEqual(len(action), min(cfg.RANKING_MIN_ROWS, n_action))   # 그 장르 게임이 20개보다 적으면 전부
        self.assertEqual((action[0]["id"], action[0]["rank"], action[0]["charted"]), ("brawl", 1, True))
        off = [g for g in action if not g["charted"]]
        self.assertTrue(all(g["rank"] is None for g in off))
        self.assertEqual([g["id"] for g in off][:2], ["soulknight", "deadcells"])    # 설치 수·별점 수 많은 순

    def test_store_tab_fills_with_that_store_links(self):
        conn = self.db()
        conn.execute("UPDATE games SET ios_url = NULL")
        conn.execute("UPDATE games SET ios_url = 'https://apps.apple.com/kr/app/id1' WHERE id IN ('candy', 'genshin')")
        conn.commit()
        ios = self.tab("ios")
        self.assertEqual([g["id"] for g in ios], ["candy", "genshin"])     # App Store 링크 있는 게임만 채움

    def test_home_and_all_tab_are_chart_only(self):
        home = self.tab("all", limit=5, fill="false")
        self.assertEqual([g["id"] for g in home], ["brawl", "candy"])
        self.assertTrue(all(g["charted"] for g in home))

    def test_ranking_not_filtered_by_dislikes(self):
        self.survey(CASUAL_HATER)
        self.assertIn("candy", [g["id"] for g in self.tab("all")])          # 퍼즐·귀여움이어도 나옴
        self.assertIn("soulknight", [g["id"] for g in self.tab("액션")])

    def test_popular_badge_stays_chart_based(self):
        badges = {g["id"]: g["badges"] for g in self.tab("액션")}
        self.assertIn("인기", badges["brawl"])
        self.assertNotIn("인기", badges["soulknight"])                       # 차트 밖은 "인기" 배지 없음


# ---------- 1. 스토어 링크 점검 ----------
class FakeClient:
    def play_details(self, package):
        if package == "com.broken.net":
            raise ConnectionError("요청 실패")
        return None if package == "com.gone" else {"title": package}

    def ios_lookup(self, ids):
        return {i: {"trackId": int(i)} for i in ids if i != "999"}


class CheckLinksTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        conn = self.db()
        conn.execute("UPDATE games SET android_url = NULL, ios_url = NULL")
        play = "https://play.google.com/store/apps/details?id="
        rows = {"dq8": (play + "com.ok", "https://apps.apple.com/kr/app/id111"),
                "ff6": (play + "com.gone", "https://apps.apple.com/kr/app/id999"),
                "chrono": (play + "com.broken.net", None)}
        for game_id, (android, ios) in rows.items():
            conn.execute("UPDATE games SET android_url = ?, ios_url = ? WHERE id = ?", (android, ios, game_id))
        conn.commit()

    def links(self, game_id):
        return tuple(self.db().execute("SELECT android_url, ios_url FROM games WHERE id = ?", (game_id,)).fetchone())

    def test_report_only(self):
        with get_connection(self.db_path) as conn:
            report = check_store_links.check_links(conn, FakeClient())
        self.assertEqual((report["checked_play"], report["checked_ios"]), (3, 2))
        self.assertEqual([g for g, _, _ in report["dead_play"]], ["ff6"])
        self.assertEqual([g for g, _, _ in report["dead_ios"]], ["ff6"])
        self.assertEqual([g for g, _, _ in report["unknown"]], ["chrono"])   # 확인 못 함 ≠ 죽음
        self.assertIsNotNone(self.links("ff6")[0])                             # 출력만

    def test_apply_clears_only_dead(self):
        self.db().execute("UPDATE games SET android_id = 'com.gone' WHERE id = 'ff6'").connection.commit()
        with get_connection(self.db_path) as conn:
            check_store_links.check_links(conn, FakeClient(), apply=True)
        self.assertEqual(self.links("ff6"), (None, None))
        self.assertIsNotNone(self.links("dq8")[0])
        self.assertIsNotNone(self.links("chrono")[0])                          # 확인 실패는 안 비움
        self.assertEqual(self.db().execute("SELECT android_id FROM games WHERE id = 'ff6'").fetchone()[0],
                         "com.gone")                                           # 스토어 ID는 남김 (다시 올라오면 수집이 채움)

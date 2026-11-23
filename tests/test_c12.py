"""
C12 홈·랭킹 개편, 태그 정리, 자동 카테고리 (docs/C12_지시.md, PROJECT.md 4.11 C12).
실행: python -m unittest discover tests   (프로젝트 최상위에서)
"""
import csv
import random
import re
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import rankings, services  # noqa: E402
from app.db import get_connection  # noqa: E402
from app.reco import reco_config as cfg  # noqa: E402
from app.reco.recommender import build_profile, recommend  # noqa: E402
from scripts import tag_review  # noqa: E402
from tests.test_api import ApiTestCase  # noqa: E402
from tests.test_sync_store import SyncTestCase  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
TODAY = date.today()

# 차트 순위 (Play, iOS)
CHART = {"chrono": (1, 3), "ff6": (None, 1), "brawl": (3, 2), "dq8": (2, None), "candy": (None, 4),
         "cookiekingdom": (6, None), "epic7": (7, None), "odin": (8, None), "mushroom": (None, 9)}


class C12Case(ApiTestCase):
    def set_charts(self):
        conn = self.db()
        conn.execute("UPDATE games SET chart_rank_android = NULL, chart_rank_ios = NULL")
        for game_id, (android, ios) in CHART.items():
            conn.execute("UPDATE games SET chart_rank_android = ?, chart_rank_ios = ? WHERE id = ?", (android, ios, game_id))
        conn.commit()

    def catalog(self):
        return services.Catalog(self.db())

    def ids(self, games):
        return [g["id"] for g in games]


# ---------- C12-a ----------
class TagCheckedTest(C12Case):
    def setUp(self):
        super().setUp()
        conn = self.db()
        conn.execute("UPDATE games SET source = 'auto', tags_checked = 0 WHERE id = 'genshin'")
        conn.commit()
        self.survey({"genres": ["RPG"]})

    def test_unchecked_not_in_match_feed_but_elsewhere(self):
        feed = self.ids(self.client.get("/api/feed?section=match&limit=30").json()["games"])
        self.assertTrue(feed)
        self.assertNotIn("genshin", feed)
        self.assertIn("genshin", self.ids(self.client.get("/api/search", params={"q": "원신"}).json()["games"]))
        self.assertEqual(self.client.get("/api/games/genshin").status_code, 200)
        self.db().execute("UPDATE games SET tags_checked = 1 WHERE id = 'genshin'").connection.commit()
        self.assertIn("genshin", self.ids(self.client.get("/api/feed?section=match&limit=30").json()["games"]))

    def test_unchecked_shows_genre_only(self):
        card = self.client.get("/api/search", params={"q": "원신"}).json()["games"][0]
        self.assertEqual(card["tags"], ["RPG"])
        detail = self.client.get("/api/games/genshin").json()
        self.assertEqual(detail["tags"]["genre"], ["RPG"])
        self.assertEqual([t for c, ts in detail["tags"].items() if c != "genre" for t in ts], [])
        self.assertFalse(detail["tags_checked"])
        checked = self.client.get("/api/games/dq8").json()   # 직접 넣은 게임은 검수된 것으로 봄
        self.assertTrue(checked["tags_checked"])
        self.assertTrue(checked["tags"]["subgenre"])


class ExploreRandomTest(C12Case):
    def test_explore_slot_varies_within_top_pool(self):
        catalog = self.catalog()
        profile = build_profile({"genres": ["RPG"]}, catalog.by_id)
        ranked = [r["game"]["id"] for r in recommend(catalog.games, profile, k=len(catalog.games), explore_ratio=0)]
        picks = set()
        for seed in range(12):
            result = recommend(catalog.games, profile, k=5, explore_ratio=0.4, rng=random.Random(seed))
            main = [r["game"]["id"] for r in result if r["slot"] == "추천"]
            explore = [r["game"]["id"] for r in result if r["slot"] == "탐색"]
            self.assertEqual(main, ranked[:3])                                   # 추천 칸은 늘 같음
            self.assertEqual(len(explore), 2)
            self.assertTrue(set(explore) <= set(ranked[3:3 + cfg.EXPLORE_POOL]))  # 상위 N개 안에서
            picks.add(tuple(explore))
        self.assertGreater(len(picks), 1)                                          # 새로고침마다 달라짐


class TagReviewToolTest(C12Case):
    def setUp(self):
        super().setUp()
        conn = self.db()
        conn.execute("UPDATE games SET source = 'auto', tags_checked = 0 WHERE id IN ('genshin', 'odin', 'epic7')")
        conn.execute("UPDATE games SET chart_rank_ios = 5 WHERE id = 'odin'")
        conn.execute("UPDATE games SET installs = 5000000 WHERE id = 'epic7'")
        conn.commit()
        self.csv = Path(self.tmp.name) / "review.csv"

    def tags(self, game_id):
        with get_connection(self.db_path) as conn:
            return tag_review.current_tags(conn, game_id)

    def test_export_order_and_import(self):
        with get_connection(self.db_path) as conn:
            self.assertEqual(tag_review.export(conn, self.csv), 3)
        rows = list(csv.DictReader(line for line in self.csv.open(encoding="utf-8-sig") if not line.startswith("#")))  # 첫 줄 안내 (C12-i)
        self.assertEqual([r["id"] for r in rows], ["odin", "epic7", "genshin"])   # 차트 순위 → 설치 수 순
        self.assertEqual(list(rows[0]), tag_review.FIELDS)
        rows[0].update(suggested_tags="RPG; MMORPG; 판타지", checked="1")         # 바꾸고 검수
        rows[1].update(checked="y")                                               # 그대로 검수만
        rows[2].update(suggested_tags="RPG; 없는태그", checked="1")                # 없는 태그 → 건너뜀
        with self.csv.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=tag_review.FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        before_epic = self.tags("epic7")
        with get_connection(self.db_path) as conn:
            report = tag_review.import_csv(conn, self.csv)
        self.assertEqual((report["changed"], report["checked_only"]), (["odin"], ["epic7"]))
        self.assertEqual([g for g, _ in report["skipped"]], ["genshin"])
        self.assertEqual(self.tags("odin"), ["genre:RPG", "subgenre:MMORPG", "mood:판타지"])
        self.assertEqual(self.tags("epic7"), before_epic)
        checked = dict(self.db().execute("SELECT id, tags_checked FROM games WHERE id IN ('odin', 'epic7', 'genshin')").fetchall())
        self.assertEqual(checked, {"odin": 1, "epic7": 1, "genshin": 0})

    def test_add_tags_one_genre_only(self):
        with get_connection(self.db_path) as conn:
            result = tag_review.add_tags(conn, "genshin", ["수집형RPG", "mood:귀여움"])
            self.assertIn("subgenre:수집형RPG", result)
            with self.assertRaises(tag_review.TagError):
                tag_review.add_tags(conn, "odin", ["캐주얼"])                      # 장르 둘은 안 됨
        self.assertEqual(self.db().execute("SELECT tags_checked FROM games WHERE id = 'genshin'").fetchone()[0], 1)


# ---------- C12-b ----------
class HomeOrderTest(unittest.TestCase):
    def test_home_sections_order(self):
        text = (BASE / "static" / "js" / "home.js").read_text(encoding="utf-8")
        block = text[text.index("app.append("):]
        order = [block.index(f"{name}()") for name in
                 ("searchBox", "popularSection", "monthlySection", "matchSection", "likesSection", "historySection")]
        self.assertEqual(order, sorted(order))   # 검색 → 인기 랭킹 → 이달의 게임 → 맞춤 추천 → 추천 순위 → 최근 본 게임


# ---------- C12-c ----------
class PopularTabsTest(C12Case):
    def setUp(self):
        super().setUp()
        self.set_charts()

    def tab(self, tab):
        return self.client.get("/api/popular", params={"tab": tab}).json()

    def test_tab_orders(self):
        body = self.tab("all")
        self.assertEqual([t["key"] for t in body["tabs"]], ["all", "play", "ios", *cfg.RANKING_GENRE_TABS])
        self.assertEqual(self.ids(body["games"])[:5], ["chrono", "ff6", "brawl", "dq8", "candy"])   # 높은 순위 → 다른 쪽 순위
        self.assertEqual([g["rank"] for g in body["games"]][:3], [1, 2, 3])
        self.assertEqual(self.ids(self.tab("play")["games"]), ["chrono", "dq8", "brawl", "cookiekingdom", "epic7", "odin"])
        self.assertEqual(self.ids(self.tab("ios")["games"]), ["ff6", "brawl", "chrono", "candy", "mushroom"])
        rpg = self.tab("RPG")["games"]
        all_rpg = [g for g in self.ids(body["games"]) if self.client.get(f"/api/games/{g}").json()["tags"]["genre"] == ["RPG"]]
        self.assertEqual(self.ids(rpg), all_rpg)                                    # 전체 순서에서 RPG만
        self.assertEqual([g["rank"] for g in rpg], list(range(1, len(rpg) + 1)))
        first = body["games"][0]
        self.assertEqual((first["chart_rank_android"], first["chart_rank_ios"]), (1, 3))
        self.assertIn("summary", first)

    def test_bad_tab(self):
        self.assertEqual(self.client.get("/api/popular", params={"tab": "없음"}).status_code, 422)


# ---------- C12-d ----------
class MonthlyTest(C12Case):
    def setUp(self):
        super().setUp()
        self.set_charts()

    def test_excludes_dislikes_and_home_top5(self):
        self.survey({"genres": ["RPG"], "dislikes": ["genre:캐주얼"]})   # 캐주얼 → 귀여움도 (연관 태그)
        ids = self.ids(self.client.get("/api/monthly").json()["games"])
        self.assertTrue(ids)
        self.assertFalse(set(ids) & {"chrono", "ff6", "brawl", "dq8", "candy"})     # 홈 인기 랭킹 상위 5개와 안 겹침
        self.assertFalse(set(ids) & {"cookiekingdom", "mushroom"})                  # 귀여움 게임 제외
        self.assertLessEqual(len(ids), cfg.MONTHLY_SIZE)

    def test_before_survey_no_dislikes(self):
        body = self.client.get("/api/monthly").json()
        self.assertEqual(body["month"], TODAY.month)
        self.assertEqual(self.ids(body["games"])[0], "cookiekingdom")                # 남은 것 중 이번 달 순위가 가장 높음


# ---------- C12-e ----------
class BadgeTest(C12Case):
    def setUp(self):
        super().setUp()
        self.set_charts()
        conn = self.db()
        conn.execute("UPDATE games SET release_date = NULL, installs = NULL")
        conn.execute("UPDATE games SET release_date = ? WHERE id = 'dq8'", ((TODAY - timedelta(days=10)).isoformat(),))
        conn.execute("UPDATE games SET release_date = ? WHERE id = 'ff6'", ((TODAY - timedelta(days=40)).isoformat(),))
        conn.execute("UPDATE games SET release_date = ? WHERE id = 'chrono'", ((TODAY + timedelta(days=3)).isoformat(),))
        conn.execute("UPDATE games SET installs = 50000 WHERE id IN ('deadcells', 'dq8')")   # deadcells 인디 / dq8 인디 아님(직접 적음)
        conn.execute("UPDATE games SET installs = 5000000 WHERE id = 'stardew'")             # 인디 개발사여도 설치 많으면 아님
        conn.execute("UPDATE games SET installs = 100000 WHERE id = 'genshin'")
        conn.execute("UPDATE games SET is_indie = NULL WHERE id = 'genshin'")                # 자동 수집처럼 미정 + 10만 → 인디
        conn.execute("UPDATE games SET is_indie = 1 WHERE id = 'gris'")                      # 설치 수 없음(iOS만) → is_indie
        conn.commit()

    def test_new_indie_popular(self):
        book = self.catalog().badges
        self.assertEqual(book.sets["신작"], {"dq8"})
        self.assertTrue({"deadcells", "genshin", "gris"} <= book.sets["인디"])
        self.assertFalse({"dq8", "stardew"} & book.sets["인디"])
        self.assertEqual(book.sets["인기"], set(CHART))                       # 차트에 든 9개 모두 30위 안
        self.assertEqual(book.of("dq8"), ["신작", "인기"])

    def test_popular_badge_is_top_30_only(self):
        conn = self.db()
        for i, (game_id,) in enumerate(conn.execute("SELECT id FROM games WHERE chart_rank_android IS NULL "
                                                    "AND chart_rank_ios IS NULL").fetchall()):
            conn.execute("UPDATE games SET chart_rank_android = ? WHERE id = ?", (20 + i, game_id))
        conn.commit()
        ranked = [g["id"] for _, g in rankings.popular(self.catalog())]
        book = self.catalog().badges
        self.assertGreater(len(ranked), cfg.POPULAR_BADGE_TOP)
        self.assertEqual(book.sets["인기"], set(ranked[:cfg.POPULAR_BADGE_TOP]))

    def snapshot(self, days_ago, game_id, rating_count, android=None, ios=None):
        self.db().execute("INSERT OR REPLACE INTO game_stats_daily (day, game_id, rating_count, chart_rank_android, "
                          "chart_rank_ios) VALUES (?, ?, ?, ?, ?)",
                          ((TODAY - timedelta(days=days_ago)).isoformat(), game_id, rating_count, android, ios)
                          ).connection.commit()

    def test_surge_needs_7_days(self):
        self.db().execute("UPDATE games SET store_rating_count = 200 WHERE id = 'brawl'").connection.commit()
        self.snapshot(3, "brawl", 100, android=50)
        book = self.catalog().badges
        self.assertTrue(book.collecting)                                             # 기록 쌓는 중
        self.assertEqual(book.sets["급상승"], set())
        self.assertTrue(self.client.get("/api/popular").json()["surge_collecting"])

    def test_surge_after_7_days(self):
        self.db().execute("UPDATE games SET store_rating_count = 200 WHERE id = 'brawl'").connection.commit()
        self.snapshot(8, "brawl", 100, android=50)          # 별점 수 2배 + 순위 50 → 2
        self.snapshot(8, "dq8", 1000, android=2)            # 변화 없음
        book = self.catalog().badges
        self.assertFalse(book.collecting)
        self.assertEqual(book.sets["급상승"], {"brawl"})
        w = cfg.SURGE_WEIGHTS
        self.assertAlmostEqual(book.surge["brawl"], w["rating"] * 1.0 + w["rank"] * 0.48, places=3)

    def test_search_badge_words(self):
        self.db().execute("UPDATE games SET store_rating_count = 200 WHERE id = 'brawl'").connection.commit()
        self.snapshot(8, "brawl", 100, android=50)
        found = lambda q: self.ids(self.client.get("/api/search", params={"q": q}).json()["games"])  # noqa: E731
        self.assertEqual(found("신작"), ["dq8"])
        self.assertTrue({"deadcells", "genshin", "gris"} <= set(found("인디")))
        self.assertNotIn("dq8", found("인디"))
        self.assertEqual(found("급상승"), ["brawl"])
        self.assertEqual(set(found("인기")), set(CHART))
        keywords = self.client.get("/api/search", params={"q": "인디"}).json()["keywords"]
        self.assertEqual(keywords[0]["kind"], "badge")

    def test_cards_and_detail_have_badges(self):
        self.assertEqual(self.client.get("/api/games/dq8").json()["badges"], ["신작", "인기"])
        card = self.client.get("/api/popular").json()["games"][0]
        self.assertIn("badges", card)


class SnapshotSyncTest(SyncTestCase):
    def test_sync_saves_daily_snapshot(self):
        from tests.test_sync_store import FakeStore
        report = sync_run = self.run_sync(FakeStore())
        self.assertGreater(sync_run["snapshot"], 0)
        with get_connection(self.db_path) as conn:
            days = conn.execute("SELECT DISTINCT day FROM game_stats_daily").fetchall()
            self.assertEqual([d[0] for d in days], [TODAY.isoformat()])
            again = self.run_sync(FakeStore())["snapshot"]                    # 같은 날 다시 돌면 덮어씀
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM game_stats_daily").fetchone()[0], again)
        self.assertTrue(report)


class SeedKeepsSnapshotsTest(unittest.TestCase):
    def test_seed_keeps_snapshots(self):
        from scripts import make_seed_db
        self.assertIn("game_stats_daily", make_seed_db.KEEP_TABLES)
        self.assertNotIn("game_stats_daily", make_seed_db.USER_TABLES)


# ---------- C12-f ----------
class NoDecorativeEmojiTest(unittest.TestCase):
    # 이모지 범위. ★(U+2605)은 별점 표시 글자라 뺌 (장식 아님)
    EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-☄☆-⛿✨]")

    def test_screen_js_has_no_emoji(self):
        """화면 코드(주석 제외)에 장식 이모지 없음. 개발 확인용 preview.js는 뺌"""
        for path in (BASE / "static" / "js").glob("*.js"):
            if path.name == "preview.js":
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("//")[0]
                self.assertIsNone(self.EMOJI.search(code), f"{path.name}:{i}: {line.strip()}")

    def test_font_sizes_four_steps(self):
        sizes = set()
        for path in (BASE / "static" / "css").glob("*.css"):
            if path.name == "preview.css":
                continue
            sizes |= {int(n) for n in re.findall(r"font-size: (\d+)px", path.read_text(encoding="utf-8"))}
        # 12·14·16·20 + 로고 19 + 썸네일 대체 글자(장식) 24·32·40
        self.assertLessEqual(sizes, {12, 14, 16, 20, 19, 24, 32, 40})


if __name__ == "__main__":
    unittest.main()

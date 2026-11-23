"""
C12-i 맞춤 추천 후보 부족 해결 (docs/C12-i_지시.md): 검수 먼저 + 모자라면 미검수로 채움, 검수 도구 보강.
실행: python -m unittest discover tests   (프로젝트 최상위에서)
"""
import csv
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import services  # noqa: E402
from app.db import get_connection  # noqa: E402
from app.reco import reco_config as cfg  # noqa: E402
from app.reco.recommender import passes_filter  # noqa: E402
from scripts import tag_review  # noqa: E402
from tests.test_api import ApiTestCase  # noqa: E402

RPG = ["dq8", "ff6", "chrono", "dnfm", "maplem", "lineagem", "odin", "genshin", "hsr", "bluearchive", "epic7",
       "summoners", "7kidle", "mushroom", "cookiekingdom", "darkest"]
CUTE_RPG = {"maplem", "bluearchive", "7kidle", "mushroom", "cookiekingdom"}


class FillTestCase(ApiTestCase):
    def only_checked(self, *checked):
        """RPG 중 checked만 검수된 게임으로 두고 나머지 RPG는 미검수 자동 수집 게임으로"""
        conn = self.db()
        conn.execute(f"UPDATE games SET source = 'auto', tags_checked = 0 WHERE id IN ({','.join('?' * len(RPG))})", RPG)
        conn.execute(f"UPDATE games SET tags_checked = 1 WHERE id IN ({','.join('?' * len(checked))})", checked)
        conn.commit()

    def pool_candidates(self):
        catalog = services.Catalog(self.db())
        user = self.db().execute("SELECT * FROM users WHERE id = ?", (self.uid(),)).fetchone()
        profile = services.profile_for(self.db(), user, catalog)
        return catalog, [g for g in services.recommendation_pool(catalog, profile) if passes_filter(g, profile)]

    def feed(self, **params):
        return self.client.get("/api/feed", params={"section": "match", "limit": 9, **params}).json()


class FillCandidatesTest(FillTestCase):
    def test_enough_checked_no_unchecked(self):
        self.only_checked(*RPG[:10])                          # 검수된 RPG 10개 ≥ 9
        self.survey({"genres": ["RPG"]})
        catalog, pool = self.pool_candidates()
        self.assertEqual(len(pool), 10)
        self.assertTrue(all(catalog.checked(g) for g in pool))
        ids = {g["id"] for g in self.feed(limit=30)["games"]}
        self.assertFalse(ids & set(RPG[10:]))

    def test_fill_up_to_9_with_unchecked(self):
        self.only_checked("dq8", "ff6", "chrono")
        self.survey({"genres": ["RPG"], "subgenres": ["턴제RPG"]})
        catalog, pool = self.pool_candidates()
        self.assertEqual(len(pool), cfg.CHECKED_MIN_CANDIDATES)
        self.assertEqual(sum(1 for g in pool if catalog.checked(g)), 3)  # 검수 3 + 미검수 6
        games = self.feed(limit=30)["games"]
        filled = [g for g in games if g["id"] not in ("dq8", "ff6", "chrono")]
        self.assertTrue(filled)
        self.assertTrue(all(g["tags"] == ["RPG"] for g in filled))        # 채운 게임 칩은 장르 하나만 (따로 표시 없음)

    def test_filled_games_respect_dislikes(self):
        self.only_checked("dq8", "ff6", "chrono")
        self.survey({"genres": ["RPG"], "dislikes": ["genre:캐주얼"]})      # 캐주얼 → 귀여움 (연관 태그)
        _, pool = self.pool_candidates()
        self.assertEqual(len(pool), cfg.CHECKED_MIN_CANDIDATES)
        self.assertFalse({g["id"] for g in pool} & CUTE_RPG)
        self.assertFalse({g["id"] for g in self.feed(limit=30)["games"]} & CUTE_RPG)

    def test_fill_order_score_band_then_tags(self):
        self.only_checked("dq8")
        self.survey({"genres": ["RPG"], "subgenres": ["턴제RPG"]})
        catalog = services.Catalog(self.db())
        user = self.db().execute("SELECT * FROM users WHERE id = ?", (self.uid(),)).fetchone()
        profile = services.profile_for(self.db(), user, catalog)
        fill = services.unchecked_fill(catalog, profile)
        bands = [int(services.score(g, profile) // cfg.FILL_SCORE_STEP) for g in fill]
        self.assertEqual(bands, sorted(bands, reverse=True))              # 점수대 높은 순
        for a, b in zip(fill, fill[1:]):                                   # 같은 점수대면 태그 많은 순
            if int(services.score(a, profile) // cfg.FILL_SCORE_STEP) == int(services.score(b, profile) // cfg.FILL_SCORE_STEP):
                self.assertGreaterEqual(len(services.game_features(a)), len(services.game_features(b)))


class HomeFillTest(FillTestCase):
    def test_home_fill_avoids_fallback(self):
        self.only_checked("dq8")                                            # 기준(60%) 넘는 검수 게임이 3개 미만
        self.new_user("feedback")  # random 그룹은 실험 대조군이라 순서가 원래 무작위 → 순서 확인은 feedback 그룹으로
        self.survey({"genres": ["RPG"], "subgenres": ["턴제RPG"], "favorites": ["ff6"]})
        with mock.patch.object(services, "unchecked_fill", lambda catalog, profile: []):
            self.assertTrue(self.feed(min_match=60)["fallback"])             # 채우기 없으면(C12) 대체 동작
        body = self.feed(min_match=60)
        self.assertFalse(body["fallback"])                                  # C12-i: 미검수로 채워 기준 칸 유지
        main = [g for g in body["games"] if g["slot"] == "추천"]
        self.assertGreaterEqual(len(main), 3)
        self.assertTrue(all(g["match"] >= 60 for g in main))
        self.assertEqual(main[0]["id"], "dq8")                               # 검수된 게임 먼저


class TagReviewC12iTest(FillTestCase):
    def setUp(self):
        super().setUp()
        self.only_checked()
        self.csv = Path(self.tmp.name) / "review.csv"

    def read(self):
        with self.csv.open(encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
        return lines[0], list(csv.DictReader(lines[1:]))

    def test_top_and_guide_line(self):
        with get_connection(self.db_path) as conn:
            self.assertEqual(tag_review.export(conn, self.csv, top=5), 5)
        guide, rows = self.read()
        self.assertTrue(guide.startswith("# 채우는 방법"))
        self.assertIn("docs/tag_list.md", guide)
        self.assertEqual(len(rows), 5)
        self.assertIn(", ", rows[0]["current_tags"])                          # 쉼표 구분

    def test_import_skips_bad_rows_with_reason(self):
        with get_connection(self.db_path) as conn:
            tag_review.export(conn, self.csv, top=4)
        guide, rows = self.read()
        rows[0].update(suggested_tags="RPG, 없는태그", checked="1")
        rows[1].update(suggested_tags="RPG, 액션, 턴제RPG", checked="1")      # 장르 2개
        rows[2].update(suggested_tags="턴제RPG, 판타지", checked="1")          # 장르 0개
        rows[3].update(suggested_tags="RPG, 턴제RPG, 판타지", checked="1")     # 정상
        with self.csv.open("w", encoding="utf-8-sig", newline="") as f:
            f.write(guide + "\r\n")
            writer = csv.DictWriter(f, fieldnames=tag_review.FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        with get_connection(self.db_path) as conn:
            report = tag_review.import_csv(conn, self.csv)
        reasons = dict(report["skipped"])
        self.assertIn("없는 태그: 없는태그", reasons[rows[0]["id"]])
        self.assertIn("2개", reasons[rows[1]["id"]])
        self.assertIn("0개", reasons[rows[2]["id"]])
        self.assertEqual(report["changed"], [rows[3]["id"]])
        checked = {r[0]: r[1] for r in self.db().execute("SELECT id, tags_checked FROM games")}
        self.assertEqual([checked[r["id"]] for r in rows], [0, 0, 0, 1])     # 건너뛴 줄은 검수 표시도 안 함

    def test_tag_list_markdown(self):
        out = Path(self.tmp.name) / "tag_list.md"
        with get_connection(self.db_path) as conn:
            count = tag_review.write_tag_list(conn, out)
        text = out.read_text(encoding="utf-8")
        self.assertGreater(count, 0)
        self.assertIn("## 장르 (꼭 하나)", text)
        self.assertIn("턴제RPG", text)
        self.assertTrue(out.name.isascii())

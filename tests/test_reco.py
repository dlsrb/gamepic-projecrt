"""
추천 점수 규칙 단위 테스트 (PROJECT.md 4.1-4, 4.11 C3).
실행: python -m unittest discover tests   (프로젝트 최상위에서)
실제 DB는 건드리지 않고, 메모리 DB에 games.json을 적재해서 쓴다.
"""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.reco import reco_config as cfg  # noqa: E402
from app.reco import repository as repo  # noqa: E402
from app.reco.recommender import build_profile, coverage, match_percent, recommend, set_best_score  # noqa: E402
from scripts import init_db  # noqa: E402

USER = "test-user"


def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(init_db.SCHEMA)
    init_db.load_games(conn, json.loads(init_db.GAMES_JSON.read_text(encoding="utf-8")))
    conn.execute("INSERT INTO users (id) VALUES (?)", (USER,))
    return conn


def score_of(conn, feature: str) -> float:
    category, name = feature.split(":", 1)
    row = conn.execute(
        "SELECT s.score FROM user_tag_scores s JOIN tags t ON t.id = s.tag_id "
        "WHERE s.user_id = ? AND t.category = ? AND t.name = ?",
        (USER, category, name),
    ).fetchone()
    return cfg.INITIAL_SCORE if row is None else row["score"]


class ConfigTest(unittest.TestCase):
    def test_confirmed_numbers(self):
        self.assertEqual(cfg.SURVEY_WEIGHT, 2.0)
        self.assertEqual(cfg.FAVORITE_WEIGHT, 1.0)
        self.assertEqual(cfg.DISLIKE_WEIGHT, -5.0)
        self.assertEqual(cfg.FEEDBACK_DELTA, {"like": 1.0, "store": 0.5, "dislike": -1.0})
        self.assertEqual((cfg.WEIGHT_MIN, cfg.WEIGHT_MAX), (-5.0, 5.0))
        self.assertEqual(cfg.INITIAL_SCORE, 0.0)
        self.assertEqual(cfg.EXPLORE_RATIO, 0.2)


class SurveyScoreTest(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.games = repo.load_games(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_load_games_matches_json(self):
        source = json.loads(init_db.GAMES_JSON.read_text(encoding="utf-8"))
        self.assertEqual(self.games, source)

    def test_survey_preference_is_plus_2(self):
        repo.save_survey(self.conn, USER, {"genres": ["RPG"], "likes": {"view": ["2D"]}})
        self.assertEqual(score_of(self.conn, "genre:RPG"), 2.0)
        self.assertEqual(score_of(self.conn, "view:2D"), 2.0)

    def test_favorite_tags_are_plus_1_and_summed(self):
        # dq8과 ff6 둘 다 RPG·턴제RPG → +1 두 번 = 2. 3D는 dq8에만 → 1
        repo.save_survey(self.conn, USER, {"favorites": ["dq8", "ff6"]})
        self.assertEqual(score_of(self.conn, "genre:RPG"), 2.0)
        self.assertEqual(score_of(self.conn, "subgenre:턴제RPG"), 2.0)
        self.assertEqual(score_of(self.conn, "view:3D"), 1.0)

    def test_survey_and_favorite_are_summed(self):
        repo.save_survey(self.conn, USER, {"genres": ["RPG"], "favorites": ["dq8"]})
        self.assertEqual(score_of(self.conn, "genre:RPG"), 3.0)

    def test_dislike_is_minus_5_and_excluded(self):
        repo.save_survey(self.conn, USER, {"genres": ["RPG"], "dislikes": ["bm:가챠"]})
        self.assertEqual(score_of(self.conn, "bm:가챠"), -5.0)
        profile = repo.load_profile(self.conn, USER)
        self.assertIn("bm:가챠", profile.excluded)
        results = recommend(self.games, profile, k=len(self.games), explore_ratio=0)
        self.assertTrue(results)
        self.assertTrue(all("가챠" not in r["game"]["bm"] for r in results))

    def test_dislike_wins_over_preference(self):
        p = build_profile({"likes": {"bm": ["가챠"]}, "dislikes": ["bm:가챠"]}, {})
        self.assertEqual(p.weights["bm:가챠"], -5.0)

    def test_untouched_tag_starts_at_0(self):
        repo.save_survey(self.conn, USER, {"genres": ["RPG"]})
        self.assertEqual(score_of(self.conn, "genre:퍼즐"), 0.0)

    def test_resurvey_replaces_scores(self):
        repo.save_survey(self.conn, USER, {"genres": ["RPG"]})
        repo.save_survey(self.conn, USER, {"genres": ["퍼즐"]})
        self.assertEqual(score_of(self.conn, "genre:RPG"), 0.0)
        self.assertEqual(score_of(self.conn, "genre:퍼즐"), 2.0)
        self.assertEqual(repo.load_survey(self.conn, USER), {"genres": ["퍼즐"]})


class ReactionTest(unittest.TestCase):
    GAME = "dq8"  # genre:RPG 포함

    def setUp(self):
        self.conn = make_db()

    def tearDown(self):
        self.conn.close()

    def count(self, type_):
        return self.conn.execute("SELECT COUNT(*) FROM reactions WHERE user_id = ? AND type = ?",
                                 (USER, type_)).fetchone()[0]

    def test_like_plus_1(self):
        self.assertTrue(repo.record_reaction(self.conn, USER, self.GAME, "like"))
        self.assertEqual(score_of(self.conn, "genre:RPG"), 1.0)

    def test_store_plus_half(self):
        self.assertTrue(repo.record_reaction(self.conn, USER, self.GAME, "store"))
        self.assertEqual(score_of(self.conn, "genre:RPG"), 0.5)

    def test_dislike_minus_1(self):
        self.assertTrue(repo.record_reaction(self.conn, USER, self.GAME, "dislike"))
        self.assertEqual(score_of(self.conn, "genre:RPG"), -1.0)

    def test_view_records_only(self):
        self.assertFalse(repo.record_reaction(self.conn, USER, self.GAME, "view"))
        self.assertEqual(score_of(self.conn, "genre:RPG"), 0.0)
        self.assertEqual(self.count("view"), 1)

    def test_like_once_per_game(self):
        repo.record_reaction(self.conn, USER, self.GAME, "like")
        self.assertFalse(repo.record_reaction(self.conn, USER, self.GAME, "like"))
        self.assertEqual(score_of(self.conn, "genre:RPG"), 1.0)
        self.assertEqual(self.count("like"), 1)

    def test_cancel_like_reverts_and_deletes_row(self):
        repo.record_reaction(self.conn, USER, self.GAME, "like")
        self.assertTrue(repo.cancel_like(self.conn, USER, self.GAME))
        self.assertEqual(score_of(self.conn, "genre:RPG"), 0.0)
        self.assertEqual(self.count("like"), 0)
        # 취소 후에는 다시 따봉 가능
        self.assertTrue(repo.record_reaction(self.conn, USER, self.GAME, "like"))

    def test_cancel_without_like_does_nothing(self):
        self.assertFalse(repo.cancel_like(self.conn, USER, self.GAME))
        self.assertEqual(score_of(self.conn, "genre:RPG"), 0.0)

    def test_store_once_per_day(self):
        repo.record_reaction(self.conn, USER, self.GAME, "store")
        self.assertFalse(repo.record_reaction(self.conn, USER, self.GAME, "store"))
        self.assertEqual(score_of(self.conn, "genre:RPG"), 0.5)
        self.assertEqual(self.count("store"), 2)  # 기록은 남는다

    def test_store_again_next_day(self):
        repo.record_reaction(self.conn, USER, self.GAME, "store")
        self.conn.execute("UPDATE reactions SET created_at = datetime('now', '-1 day')")
        self.assertTrue(repo.record_reaction(self.conn, USER, self.GAME, "store"))
        self.assertEqual(score_of(self.conn, "genre:RPG"), 1.0)

    def test_store_limit_is_per_game(self):
        repo.record_reaction(self.conn, USER, self.GAME, "store")
        self.assertTrue(repo.record_reaction(self.conn, USER, "ff6", "store"))
        self.assertEqual(score_of(self.conn, "genre:RPG"), 1.0)

    def test_score_clamped_to_plus_5(self):
        repo.save_survey(self.conn, USER, {"genres": ["RPG"], "favorites": ["ff6", "chrono"]})  # RPG = 2 + 1 + 1 = 4
        for gid in ("dq8", "dnfm", "maplem"):
            repo.record_reaction(self.conn, USER, gid, "like")
        self.assertEqual(score_of(self.conn, "genre:RPG"), 5.0)

    def test_score_clamped_to_minus_5(self):
        for gid in ("dq8", "ff6", "chrono", "dnfm", "maplem", "genshin"):
            repo.record_reaction(self.conn, USER, gid, "dislike")
        self.assertEqual(score_of(self.conn, "genre:RPG"), -5.0)

    def test_reacted_games_are_not_recommended_again(self):
        repo.save_survey(self.conn, USER, {"genres": ["RPG"]})
        repo.record_reaction(self.conn, USER, self.GAME, "like")
        repo.record_reaction(self.conn, USER, "ff6", "view")
        profile = repo.load_profile(self.conn, USER)
        self.assertIn(self.GAME, profile.seen)
        self.assertNotIn("ff6", profile.seen)  # 상세 조회만 한 게임은 계속 추천 대상


class RecomputeTest(unittest.TestCase):
    """C3-보완: 비선호 제외 기준 + 점수 재계산"""
    RPG_GAMES = ("dq8", "ff6", "chrono", "dnfm", "maplem", "genshin")

    def setUp(self):
        self.conn = make_db()
        self.games = repo.load_games(self.conn)

    def tearDown(self):
        self.conn.close()

    def all_scores(self) -> dict[int, float]:
        rows = self.conn.execute("SELECT tag_id, score FROM user_tag_scores WHERE user_id = ?", (USER,))
        return {row["tag_id"]: row["score"] for row in rows if row["score"] != cfg.INITIAL_SCORE}

    def candidates(self) -> list[dict]:
        profile = repo.load_profile(self.conn, USER)
        return [r["game"] for r in recommend(self.games, profile, k=len(self.games), explore_ratio=0)]

    def test_dislike_feedback_to_minus_5_does_not_exclude(self):
        repo.save_survey(self.conn, USER, {})
        for gid in self.RPG_GAMES:
            repo.record_reaction(self.conn, USER, gid, "dislike")
        self.assertEqual(score_of(self.conn, "genre:RPG"), -5.0)
        self.assertNotIn("genre:RPG", repo.load_profile(self.conn, USER).excluded)
        rpg_left = [g["id"] for g in self.candidates() if g["genre"] == "RPG"]
        self.assertTrue(rpg_left)  # 반응한 6개 말고 다른 RPG 게임은 후보에 남는다
        self.assertFalse(set(rpg_left) & set(self.RPG_GAMES))

    def test_survey_dislike_still_excludes(self):
        repo.save_survey(self.conn, USER, {"dislikes": ["bm:가챠"]})
        self.assertEqual(repo.load_profile(self.conn, USER).excluded, {"bm:가챠"})
        self.assertTrue(all("가챠" not in g["bm"] for g in self.candidates()))

    def test_set_disliked_tags_changes_exclusion_and_scores(self):
        repo.save_survey(self.conn, USER, {"genres": ["RPG"], "dislikes": ["bm:가챠"]})
        repo.record_reaction(self.conn, USER, "dq8", "like")
        repo.set_disliked_tags(self.conn, USER, ["play:PvP"])
        self.assertEqual(repo.load_profile(self.conn, USER).excluded, {"play:PvP"})
        self.assertEqual(score_of(self.conn, "bm:가챠"), 0.0)
        self.assertEqual(score_of(self.conn, "play:PvP"), -5.0)
        self.assertEqual(score_of(self.conn, "genre:RPG"), 3.0)  # 설문 +2 + 따봉 +1 유지
        self.assertEqual(repo.load_survey(self.conn, USER)["genres"], ["RPG"])

    def test_cancel_like_at_cap_restores_plus_5(self):
        repo.save_survey(self.conn, USER, {"genres": ["RPG"], "favorites": ["ff6", "chrono", "dnfm"]})  # 2+1+1+1
        self.assertEqual(score_of(self.conn, "genre:RPG"), 5.0)
        repo.record_reaction(self.conn, USER, "dq8", "like")
        self.assertEqual(score_of(self.conn, "genre:RPG"), 5.0)
        repo.cancel_like(self.conn, USER, "dq8")
        self.assertEqual(score_of(self.conn, "genre:RPG"), 5.0)

    def test_cancel_like_keeps_other_reactions(self):
        repo.save_survey(self.conn, USER, {"genres": ["RPG"]})
        before = self.all_scores()
        repo.record_reaction(self.conn, USER, "ff6", "store")
        after_store = self.all_scores()
        repo.record_reaction(self.conn, USER, "dq8", "like")
        repo.cancel_like(self.conn, USER, "dq8")
        self.assertEqual(self.all_scores(), after_store)
        self.assertNotEqual(after_store, before)

    def test_incremental_equals_recompute(self):
        repo.save_survey(self.conn, USER, {"genres": ["RPG"], "likes": {"view": ["2D"]},
                                           "dislikes": ["bm:가챠"], "favorites": ["ff6"]})
        sequence = [
            ("dq8", "like"), ("dq8", "like"), ("dq8", "store"), ("dq8", "store"), ("dq8", "store"),
            ("dnfm", "store"), ("maplem", "dislike"), ("maplem", "dislike"), ("chrono", "like"),
            ("dq8", "view"), ("candy", "dislike"),
        ] + [(gid, "like") for gid in ("dnfm", "maplem", "darkest", "deadcells")]  # 상한 넘김 포함
        for gid, type_ in sequence:
            repo.record_reaction(self.conn, USER, gid, type_)
        incremental = self.all_scores()
        repo.recompute_scores(self.conn, USER)
        self.assertEqual(self.all_scores(), incremental)

    def test_recompute_store_same_day_counts_once_other_day_counts_again(self):
        repo.save_survey(self.conn, USER, {})
        for _ in range(3):
            repo.record_reaction(self.conn, USER, "dq8", "store")
        self.conn.execute("UPDATE reactions SET created_at = datetime('now', '-1 day') WHERE id = 1")
        repo.recompute_scores(self.conn, USER)
        self.assertEqual(score_of(self.conn, "genre:RPG"), 1.0)  # 어제 1회 + 오늘 1회


class ExploreSlotTest(unittest.TestCase):
    def test_explore_is_20_percent(self):
        conn = make_db()
        games = repo.load_games(conn)
        conn.close()
        profile = build_profile({"genres": ["RPG"]}, {g["id"]: g for g in games})
        results = recommend(games, profile, k=10)
        self.assertEqual(len(results), 10)
        self.assertEqual(sum(r["slot"] == "탐색" for r in results), 2)


class MatchPercentTest(unittest.TestCase):
    """화면 취향 % (C7-e): 일치율 70% + 1등 대비 30%, 추천 순서와는 따로"""

    def setUp(self):
        self.games = json.loads(init_db.GAMES_JSON.read_text(encoding="utf-8"))
        self.by_id = {g["id"]: g for g in self.games}

    def profile(self, survey):
        p = build_profile(survey, self.by_id)
        set_best_score(p, self.games)
        return p

    def test_range_0_to_99(self):
        for survey in ({"genres": ["RPG"]}, {"genres": ["RPG"], "subgenres": ["턴제RPG"], "favorites": ["dq8", "ff6"]},
                       {"favorites": ["candy"]}, {}):
            p = self.profile(survey)
            values = [match_percent(g, p) for g in self.games]
            self.assertTrue(all(isinstance(v, int) and 0 <= v <= cfg.MATCH_MAX for v in values), survey)
        self.assertEqual(max(match_percent(g, self.profile({})) for g in self.games), 0)  # 빈 취향

    def test_disliked_tag_lowers_percent(self):
        liked = self.profile({"genres": ["RPG"], "subgenres": ["MMORPG"]})
        disliked = self.profile({"genres": ["RPG"], "subgenres": ["MMORPG"], "dislikes": ["play:PvP"]})
        game = next(g for g in self.games if g["genre"] == "RPG" and "MMORPG" in g["subgenre"] and "PvP" in g["play"])
        self.assertLess(match_percent(game, disliked), match_percent(game, liked))

    def test_best_game_gets_relative_full(self):
        p = self.profile({"genres": ["RPG"], "subgenres": ["턴제RPG"], "favorites": ["dq8"]})
        top = recommend(self.games, p, k=1, explore_ratio=0)[0]["game"]
        self.assertEqual(match_percent(top, p), min(cfg.MATCH_MAX, round(100 * (
            cfg.MATCH_COVERAGE_RATIO * coverage(top, p) + cfg.MATCH_RELATIVE_RATIO))))

    def test_few_tags_capped(self):
        """C7-f: 태그 2개짜리 게임은 둘 다 취향이어도 일치율 최대 50%"""
        p = self.profile({"genres": ["캐주얼"], "likes": {"bm": ["유료구매"]}})
        two = {"id": "two", "name": "두 태그", "genre": "캐주얼", "subgenre": [], "view": [], "play": [], "mood": [],
               "bm": ["유료구매"], "indie": False}
        self.assertAlmostEqual(coverage(two, p), 0.5)
        # 화면 % = 0.7 × 일치율 + 0.3 × 1등 대비 → 태그 2개면 최대 0.7 × 50 + 30 = 65 (지시서 테스트의 "≤ 50"은 일치율 기준)
        self.assertLessEqual(match_percent(two, p), 65)
        four = dict(two, view=["2D"], mood=["힐링"])
        self.assertEqual(coverage(four, p), coverage(four, p) / min(1.0, 4 / cfg.MATCH_MIN_TAGS))  # 4개면 안 깎임

    def test_recommend_order_unchanged(self):
        """추천 순서는 score(코사인) 그대로 — 취향 %를 바꿔도 같다"""
        survey = {"genres": ["RPG"], "subgenres": ["턴제RPG"], "favorites": ["dq8"]}
        plain = build_profile(survey, self.by_id)
        before = [(r["game"]["id"], r["slot"]) for r in recommend(self.games, plain, k=10)]
        after = [(r["game"]["id"], r["slot"]) for r in recommend(self.games, self.profile(survey), k=10)]
        self.assertEqual(before, after)
        scores = [r["score"] for r in recommend(self.games, plain, k=10, explore_ratio=0)]
        self.assertEqual(scores, sorted(scores, reverse=True))


if __name__ == "__main__":
    unittest.main()

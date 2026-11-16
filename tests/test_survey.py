"""
설문 문항 → 엔진 형식 변환·검사 테스트 (PROJECT.md 4.12, 4.11 C4-설문).
실행: python -m unittest discover tests   (프로젝트 최상위에서)
실제 data/survey.json + 메모리 DB(games.json 적재)를 쓴다.
"""
import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.reco import repository as repo  # noqa: E402
from app.reco import survey_map  # noqa: E402
from app.reco.recommender import build_profile, recommend  # noqa: E402
from scripts import init_db  # noqa: E402

REAL_SURVEY = Path(__file__).resolve().parent.parent / "data" / "survey.json"
USER = "survey-user"
REQUIRED = {"q1": ["ios"], "q2": ["any"], "q3": ["genre:퍼즐", "genre:캐주얼"], "q4": ["none"], "q5": []}


class SurveyMapTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(config, "SURVEY_PATH", REAL_SURVEY)
        patch.start()
        self.addCleanup(patch.stop)
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript(init_db.SCHEMA)
        init_db.load_games(self.conn, json.loads(init_db.GAMES_JSON.read_text(encoding="utf-8")))
        self.conn.execute("INSERT INTO users (id) VALUES (?)", (USER,))
        self.questions = survey_map.resolved_questions(self.conn)
        self.games = repo.load_games(self.conn)

    def engine(self, extra: dict) -> dict:
        answers = {**REQUIRED, **extra}
        self.assertEqual(survey_map.validate(self.questions, answers), [])
        return survey_map.to_engine(self.questions, answers)

    # ---------- 문항별 변환 ----------
    def test_minimum_answers(self):
        self.assertEqual(self.engine({}), {"likes": {}, "platform": "ios", "genres": ["퍼즐", "캐주얼"]})

    def test_q2_free_only_dislikes_paid(self):
        self.assertEqual(self.engine({"q2": ["free_only"]})["dislikes"], ["bm:유료구매"])

    def test_q2_1_max_price_only_saved(self):
        survey = self.engine({"q2": ["paid_ok"], "q2_1": ["30000"]})
        self.assertEqual(survey["max_price"], 30000)
        self.assertNotIn("dislikes", survey)

    def test_q3_subgenre_only_fills_genre(self):
        survey = self.engine({"q3": ["subgenre:턴제RPG", "subgenre:FPS"]})
        self.assertEqual(survey["subgenres"], ["턴제RPG", "FPS"])
        self.assertEqual(survey["genres"], ["RPG", "슈팅"])

    def test_q4_dislikes_tags(self):
        survey = self.engine({"q4": ["play:PvP", "mood:밀리터리", "subgenre:MOBA"]})
        self.assertEqual(survey["dislikes"], ["play:PvP", "mood:밀리터리", "subgenre:MOBA"])

    def test_q5_favorites(self):
        self.assertEqual(self.engine({"q5": ["dq8", "stardew"]})["favorites"], ["dq8", "stardew"])

    def test_q6_bm(self):
        self.assertEqual(self.engine({"q6": ["no_both"]})["dislikes"], ["bm:광고", "bm:가챠"])
        self.assertEqual(self.engine({"q6": ["f2p_ok"]})["likes"], {"bm": ["무과금가능"]})
        self.assertNotIn("dislikes", self.engine({"q6": ["any"]}))

    def test_q7_q13_tag_likes(self):
        survey = self.engine({"q7": ["view:2D", "view:픽셀아트"], "q13": ["mood:힐링"]})
        self.assertEqual(survey["likes"], {"view": ["2D", "픽셀아트"], "mood": ["힐링"]})

    def test_q8_only_short_counts(self):
        self.assertEqual(self.engine({"q8": ["short"]})["likes"], {"play": ["짧은플레이"]})
        self.assertEqual(self.engine({"q8": ["long"]})["likes"], {})

    def test_q9_styles(self):
        survey = self.engine({"q9": ["growth", "compete", "explore", "collect", "healing", "brain"]})
        self.assertEqual(survey["likes"], {"play": ["PvP", "오픈월드"], "mood": ["힐링"]})
        self.assertEqual(survey["subgenres"], ["수집형RPG"])
        self.assertEqual(survey["boost"], ["genre:퍼즐"])
        self.assertEqual(survey["genres"], ["퍼즐", "캐주얼"])  # 두뇌는 장르 필터에 안 들어감 (3번 답 그대로)

    def test_q10_q11_saved_only(self):
        survey = self.engine({"q10": ["avoid"], "q11": ["hard"]})
        self.assertEqual((survey["ai_ok"], survey["difficulty"]), (False, "hard"))

    def test_q12_play_together(self):
        self.assertEqual(self.engine({"q12": ["solo"]})["likes"], {"play": ["싱글"]})
        self.assertEqual(self.engine({"q12": ["together"]})["likes"], {"play": ["멀티"]})

    def test_q14_offline(self):
        self.assertEqual(self.engine({"q14": ["must"]})["require"], ["play:오프라인"])
        self.assertEqual(self.engine({"q14": ["nice"]})["likes"], {"play": ["오프라인"]})

    def test_dislike_wins_over_like(self):
        survey = self.engine({"q4": ["mood:힐링"], "q13": ["mood:힐링"]})
        self.assertEqual(build_profile(survey, {}).weights["mood:힐링"], -5.0)

    # ---------- 검사 ----------
    def test_conditional_hidden_answer_ignored_in_conversion(self):
        answers = {**REQUIRED, "q2_1": ["5000"]}
        self.assertTrue(survey_map.validate(self.questions, answers))
        self.assertNotIn("max_price", survey_map.to_engine(self.questions, answers))

    def test_optional_questions_can_be_skipped(self):
        self.assertEqual(survey_map.validate(self.questions, REQUIRED), [])

    # ---------- 엔진 ----------
    def test_must_offline_filters_candidates(self):
        survey = self.engine({"q3": ["genre:RPG", "subgenre:턴제RPG"], "q14": ["must"]})
        profile = build_profile(survey, {g["id"]: g for g in self.games})
        games = [r["game"] for r in recommend(self.games, profile, k=50, explore_ratio=0)]
        self.assertTrue(games)
        self.assertTrue(all("오프라인" in g["play"] for g in games))
        without = build_profile(self.engine({"q3": ["genre:RPG", "subgenre:턴제RPG"]}), {})
        self.assertGreater(len(recommend(self.games, without, k=50, explore_ratio=0)), len(games))

    def test_brain_boost_adds_score_without_filter(self):
        survey = self.engine({"q3": ["genre:RPG", "genre:액션"], "q9": ["brain"]})
        profile = build_profile(survey, {})
        self.assertEqual(profile.weights["genre:퍼즐"], 2.0)
        self.assertEqual(profile.genres, {"RPG", "액션"})

    # ---------- 저장 → 재계산 ----------
    def test_saved_answers_drive_profile(self):
        repo.save_answers(self.conn, USER, {**REQUIRED, "q14": ["must"], "q6": ["no_ads"]})
        profile = repo.load_profile(self.conn, USER)
        self.assertEqual(profile.required, {"play:오프라인"})
        self.assertEqual(profile.excluded, {"bm:광고"})
        self.assertEqual(profile.genres, {"퍼즐", "캐주얼"})
        self.assertEqual(profile.weights["genre:퍼즐"], 2.0)
        self.assertEqual(profile.weights["bm:광고"], -5.0)

    def test_settings_dislikes_override_then_resurvey_resets(self):
        repo.save_answers(self.conn, USER, {**REQUIRED, "q6": ["no_ads"]})
        repo.set_disliked_tags(self.conn, USER, ["play:PvP"])
        self.assertEqual(repo.load_profile(self.conn, USER).excluded, {"play:PvP"})
        repo.save_answers(self.conn, USER, {**REQUIRED, "q6": ["no_gacha"]})  # 설문 다시 하기
        self.assertEqual(repo.load_profile(self.conn, USER).excluded, {"bm:가챠"})


if __name__ == "__main__":
    unittest.main()

"""
API 테스트 (PROJECT.md 4.10, 4.11 C4). FastAPI TestClient 사용.
실행: python -m unittest discover tests   (프로젝트 최상위에서)
테스트마다 임시 폴더에 새 DB를 만들어 쓴다 (data/gamepick.db는 건드리지 않음).
"""
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.db import get_connection, get_db  # noqa: E402
from app.deps import COOKIE_NAME  # noqa: E402
from app.main import app  # noqa: E402
from app.security import RATE_LIMITS, limiter  # noqa: E402
from scripts import init_db  # noqa: E402

from app.reco import repository as repo  # noqa: E402

REAL_SURVEY = Path(__file__).resolve().parent.parent / "data" / "survey.json"  # 4.12 확정 문항
# 필수 1~5번만 답한 최소 응답
MIN_ANSWERS = {"q1": ["android"], "q2": ["any"], "q3": ["genre:RPG", "subgenre:턴제RPG"], "q4": ["none"],
               "q5": []}


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        # 정리는 addCleanup으로 (나중에 등록한 것부터 실행 → 임시 폴더 삭제가 맨 마지막, DB 연결이 다 닫힌 뒤)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "test.db"
        with get_connection(self.db_path) as conn:
            conn.executescript(init_db.SCHEMA)
            init_db.load_games(conn, json.loads(init_db.GAMES_JSON.read_text(encoding="utf-8")))

        def _test_db():
            with get_connection(self.db_path) as conn:
                yield conn

        app.dependency_overrides[get_db] = _test_db
        self.addCleanup(app.dependency_overrides.clear)
        limiter.reset()
        self.survey_path = Path(self.tmp.name) / "survey.json"  # 기본은 파일 없음
        patch = mock.patch.object(config, "SURVEY_PATH", self.survey_path)
        patch.start()
        self.addCleanup(patch.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    # ---------- 도우미 ----------
    def db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn

    def uid(self, client=None) -> str:
        return (client or self.client).cookies[COOKIE_NAME]

    def new_user(self, group=None, client=None) -> TestClient:
        client = client or self.client
        client.post("/api/users")
        if group:
            self.db().execute("UPDATE users SET exp_group = ? WHERE id = ?", (group, self.uid(client))).connection.commit()
        return client

    def survey(self, body, client=None):
        """엔진 형식 설문을 바로 저장 (설문 문항 API를 거치지 않는 간단한 준비용)"""
        client = client or self.client
        client.post("/api/users")
        with get_connection(self.db_path) as conn:
            repo.save_survey(conn, self.uid(client), body)

    def use_real_survey(self):
        self.survey_path.write_bytes(REAL_SURVEY.read_bytes())

    def answer(self, answers):
        return self.client.post("/api/survey/answers", json={"answers": answers})

    def debug(self, on=True):
        p = mock.patch.object(config, "DEBUG", on)
        p.start()
        self.addCleanup(p.stop)


class UserTest(ApiTestCase):
    def test_cookie_issued_once(self):
        r = self.client.post("/api/users")
        self.assertEqual(r.json(), {"nickname": None, "survey_done": False})
        cookie = r.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)
        self.assertIn("Max-Age=31536000", cookie)
        uid = self.uid()
        r = self.client.get("/api/me")
        self.assertNotIn("set-cookie", r.headers)
        self.assertEqual(self.uid(), uid)
        row = self.db().execute("SELECT exp_group FROM users WHERE id = ?", (uid,)).fetchone()
        self.assertIn(row["exp_group"], ("random", "survey", "feedback"))

    def test_forged_cookie_gets_new_user(self):
        for fake in ("not-a-uuid", "00000000-0000-4000-8000-000000000000"):
            client = TestClient(app, cookies={COOKIE_NAME: fake})
            self.addCleanup(client.close)
            r = client.get("/api/me")
            self.assertNotEqual(r.cookies[COOKIE_NAME], fake)
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM users").fetchone()[0], 2)

    def test_id_and_group_not_exposed(self):
        body = self.new_user().get("/api/me").json()
        self.assertNotIn("id", body)
        self.assertNotIn("exp_group", body)


class ErrorFormatTest(ApiTestCase):
    def test_404_and_422_use_error_key(self):
        r = self.client.get("/api/games/nope")
        self.assertEqual((r.status_code, list(r.json())), (404, ["error"]))
        r = self.client.get("/api/games/bad id!")
        self.assertEqual((r.status_code, list(r.json())), (422, ["error"]))
        r = self.client.post("/api/reactions", json={"game_id": "dq8", "type": "hack"})
        self.assertEqual((r.status_code, list(r.json())), (422, ["error"]))

    def test_security_headers_on_api(self):
        r = self.client.get("/api/health")
        self.assertIn("default-src 'self'", r.headers["content-security-policy"])


class SurveyTest(ApiTestCase):
    def test_questions_not_ready_without_file(self):
        r = self.client.get("/api/survey/questions")
        self.assertEqual(r.status_code, 503)
        self.assertIn("error", r.json())

    def test_answers_not_ready_without_file(self):
        self.assertEqual(self.answer(MIN_ANSWERS).status_code, 503)

    def test_questions_14_plus_conditional(self):
        self.use_real_survey()
        qs = self.client.get("/api/survey/questions").json()["questions"]
        self.assertEqual(len(qs), 15)
        self.assertEqual(sum(1 for q in qs if "show_if" in q), 1)
        self.assertEqual([q["id"] for q in qs if q.get("required")], ["q1", "q2", "q3", "q4", "q5"])
        by_id = {q["id"]: q for q in qs}
        self.assertEqual(len(by_id["q3"]["options"]), 8 + 22)  # 장르 + 세부 장르
        self.assertEqual(len(by_id["q5"]["options"]), 36 + 1)  # 게임 + 없어요
        self.assertNotIn("engine", by_id["q2"]["options"][0])  # 엔진 반영 방법은 화면에 안 보냄
        turn = next(o for o in by_id["q3"]["options"] if o["value"] == "subgenre:턴제RPG")
        self.assertEqual(turn["genres"], ["RPG"])

    def test_save_answers_to_feed(self):
        self.use_real_survey()
        r = self.answer({**MIN_ANSWERS, "q4": ["play:PvP"], "q5": ["dq8"], "q6": ["no_gacha"]})
        self.assertEqual(r.status_code, 200, r.text)
        me = self.client.get("/api/me").json()
        self.assertTrue(me["survey_done"])
        self.assertEqual(set(me["disliked_tags"]), {"play:PvP", "bm:가챠"})
        games = self.client.get("/api/feed?limit=30").json()["games"]
        self.assertTrue(games)
        self.assertTrue(all(g["genre"] == "RPG" for g in games))
        self.assertTrue(all("가챠" not in g["tags"] and "PvP" not in g["tags"] for g in games))
        self.assertNotIn("dq8", [g["id"] for g in games])
        raw = self.client.get("/api/me/export").json()["survey_answers"]
        self.assertEqual(raw["q1"], ["android"])

    def test_invalid_answers_rejected(self):
        self.use_real_survey()
        cases = [
            {k: v for k, v in MIN_ANSWERS.items() if k != "q1"},           # 필수 빠짐
            {**MIN_ANSWERS, "q1": ["android", "ios"]},                     # 1개만
            {**MIN_ANSWERS, "q3": ["genre:RPG"]},                          # 2개 이상
            {**MIN_ANSWERS, "q4": []},                                     # 1개 이상
            {**MIN_ANSWERS, "q4": ["none", "play:PvP"]},                   # '딱히 없어요'는 혼자만
            {**MIN_ANSWERS, "q5": ["dq8", "ff6", "chrono", "dnfm"]},       # 최대 3
            {**MIN_ANSWERS, "q1": ["windows"]},                            # 없는 보기
            {**MIN_ANSWERS, "q99": ["x"]},                                 # 없는 문항
            {**MIN_ANSWERS, "q2_1": ["5000"]},                             # 2번이 '유료도 괜찮아요'가 아님
        ]
        for answers in cases:
            r = self.answer(answers)
            self.assertEqual(r.status_code, 422, answers)
            self.assertIn("error", r.json())
        self.assertEqual(self.client.post("/api/survey/answers", json={"genres": ["RPG"]}).status_code, 422)

    def test_conditional_question_allowed_when_shown(self):
        self.use_real_survey()
        r = self.answer({**MIN_ANSWERS, "q2": ["paid_ok"], "q2_1": ["10000"]})
        self.assertEqual(r.status_code, 200, r.text)


class FeedTest(ApiTestCase):
    def test_match_feed_feedback_group(self):
        self.new_user("feedback")
        self.survey({"genres": ["RPG"]})
        games = self.client.get("/api/feed?section=match").json()["games"]
        self.assertEqual(len(games), 10)
        self.assertTrue(all(g["genre"] == "RPG" for g in games))
        self.assertEqual(sum(g["slot"] == "탐색" for g in games), 2)
        self.assertTrue(all(isinstance(g["match"], int) and 0 <= g["match"] <= 100 for g in games))
        self.assertEqual(set(games[0]), {"id", "name", "genre", "tags", "tier", "indie", "thumbnail_url",
                                         "match", "slot", "reasons"})

    def test_random_group_filters_only(self):
        self.new_user("random")
        self.survey({"genres": ["RPG"], "dislikes": ["bm:가챠"]})
        games = self.client.get("/api/feed?limit=30").json()["games"]
        self.assertTrue(games)
        self.assertTrue(all(g["genre"] == "RPG" and "가챠" not in g["tags"] for g in games))
        self.assertTrue(all(g["slot"] == "추천" for g in games))

    def match_by_id(self):
        return {g["id"]: g["match"] for g in self.client.get("/api/feed?limit=30").json()["games"]}

    def test_survey_group_ignores_reactions(self):
        self.new_user("survey")
        self.survey({"genres": ["RPG"]})
        before = self.match_by_id()
        self.client.post("/api/reactions", json={"game_id": "dq8", "type": "like"})
        after = self.match_by_id()
        self.assertNotIn("dq8", after)  # 반응한 게임은 다시 추천 안 함 (세 그룹 공통)
        self.assertEqual(after, {k: v for k, v in before.items() if k != "dq8"})

    def test_feedback_group_uses_reactions(self):
        self.new_user("feedback")
        self.survey({"genres": ["RPG"]})
        before = self.match_by_id()
        self.client.post("/api/reactions", json={"game_id": "dq8", "type": "like"})
        after = self.match_by_id()
        self.assertNotEqual(after, {k: v for k, v in before.items() if k != "dq8"})

    def test_new_is_empty_without_release_dates(self):
        self.assertEqual(self.client.get("/api/feed?section=new").json()["games"], [])
        recent = (date.today() - timedelta(days=30)).isoformat()
        self.db().execute("UPDATE games SET release_date = ? WHERE id = 'gris'", (recent,)).connection.commit()
        self.assertEqual([g["id"] for g in self.client.get("/api/feed?section=new").json()["games"]], ["gris"])

    def test_popular_and_ranking(self):
        # 인기 = 스토어 차트 순위 (C7-a2), 추천 순위 = 따봉 수
        conn = self.db()
        conn.execute("UPDATE games SET chart_rank_android = 5, chart_rank_ios = 40 WHERE id = 'brawl'")
        conn.execute("UPDATE games SET chart_rank_ios = 2 WHERE id = 'candy'")
        conn.commit()
        popular = self.client.get("/api/feed?section=popular").json()["games"]
        self.assertEqual([(g["id"], g["chart_rank"]) for g in popular], [("candy", 2), ("brawl", 5)])
        other = TestClient(app)
        self.addCleanup(other.close)
        for client, gid in ((self.client, "dq8"), (other, "dq8"), (self.client, "ff6"), (other, "chrono")):
            client.post("/api/reactions", json={"game_id": gid, "type": "like"})
        ranking = self.client.get("/api/ranking").json()["ranking"]
        self.assertEqual([(g["id"], g["rank"]) for g in ranking], [("dq8", 1), ("ff6", 2), ("chrono", 2)])

    def test_invalid_section(self):
        self.assertEqual(self.client.get("/api/feed?section=hot").status_code, 422)
        self.assertEqual(self.client.get("/api/feed?limit=999").status_code, 422)

    # ---------- C7-d: 취향 min_match% 이상만 ----------
    def test_min_match_out_of_range(self):
        for value in ("-1", "101", "abc"):
            r = self.client.get(f"/api/feed?section=match&min_match={value}")
            self.assertEqual((r.status_code, list(r.json())), (422, ["error"]), value)

    def test_min_match_filters_except_explore(self):
        self.new_user("feedback")
        # C7-e: 새 취향 %는 태그 2개만 고른 취향이면 70%를 못 넘김 → 좋아하는 게임(dq8)까지 넣은 취향으로
        self.survey({"genres": ["RPG"], "subgenres": ["턴제RPG"], "favorites": ["dq8"]})
        body = self.client.get("/api/feed?section=match&limit=9&min_match=70").json()
        games = body["games"]
        self.assertFalse(body["fallback"])
        main = [g for g in games if g["slot"] == "추천"]
        explore = [g for g in games if g["slot"] == "탐색"]
        self.assertGreaterEqual(len(main), 3)
        self.assertTrue(all(g["match"] >= 70 for g in main))
        # 순서는 추천 점수(score) 순 그대로 = 기준 없는 목록에서의 순서와 같음 (C7-e부터 % 순서와는 다를 수 있음)
        plain = [g["id"] for g in self.client.get("/api/feed?section=match&limit=30").json()["games"]]
        self.assertEqual([g["id"] for g in main], [i for i in plain if i in {g["id"] for g in main}])
        self.assertIn(len(explore), (1, 2))
        self.assertLessEqual(len(games), 9)
        self.assertEqual(len({g["id"] for g in games}), len(games))  # 중복 없음
        self.assertTrue(all(g["genre"] == "RPG" for g in games))     # 탐색도 후보 거르기는 그대로

    def test_min_match_fallback_when_too_few(self):
        self.new_user("feedback")
        self.survey({"genres": ["RPG"]})
        plain = self.client.get("/api/feed?section=match&limit=9").json()
        self.assertFalse(plain["fallback"])
        body = self.client.get("/api/feed?section=match&limit=9&min_match=100").json()
        self.assertTrue(body["fallback"])
        self.assertEqual([g["id"] for g in body["games"]], [g["id"] for g in plain["games"]])
        self.assertEqual(len(body["games"]), 9)

    def test_min_match_random_group(self):
        self.new_user("random")
        self.survey({"genres": ["RPG"], "subgenres": ["턴제RPG"], "favorites": ["dq8"]})  # C7-e: 위와 같은 이유
        body = self.client.get("/api/feed?section=match&limit=9&min_match=70").json()
        self.assertFalse(body["fallback"])
        self.assertGreaterEqual(len(body["games"]), 3)
        self.assertTrue(all(g["match"] >= 70 and g["slot"] == "추천" for g in body["games"]))
        body = self.client.get("/api/feed?section=match&limit=9&min_match=100").json()
        self.assertTrue(body["fallback"])
        self.assertEqual(len(body["games"]), 9)


class DebugGroupTest(ApiTestCase):
    def test_group_api_only_in_debug(self):
        self.debug(False)
        self.assertEqual(self.client.put("/api/me/exp-group", json={"group": "random"}).status_code, 404)

    def test_group_api_in_debug(self):
        self.debug(True)
        r = self.client.put("/api/me/exp-group", json={"group": "survey"})
        self.assertEqual(r.json(), {"exp_group": "survey"})
        self.assertEqual(self.client.get("/api/me").json()["exp_group"], "survey")


class DetailTest(ApiTestCase):
    def test_detail_records_view(self):
        self.survey({"genres": ["RPG"]})
        body = self.client.get("/api/games/dq8").json()
        self.assertEqual(body["name"], "드래곤 퀘스트 VIII")
        self.assertEqual(body["tags"]["genre"], ["RPG"])
        self.assertIsNone(body["thumbnail_url"])
        self.assertEqual((body["like_count"], body["liked"], body["rank"]), (0, False, None))
        self.assertGreater(body["match"], 0)
        history = self.client.get("/api/me/history").json()["games"]
        self.assertEqual([g["id"] for g in history], ["dq8"])

    def test_history_latest_first_without_duplicates(self):
        for gid in ("dq8", "ff6", "dq8"):
            self.client.get(f"/api/games/{gid}")
        self.assertEqual([g["id"] for g in self.client.get("/api/me/history").json()["games"]], ["dq8", "ff6"])

    def test_detail_like_rank(self):
        self.client.post("/api/reactions", json={"game_id": "dq8", "type": "like"})
        body = self.client.get("/api/games/dq8").json()
        self.assertEqual((body["like_count"], body["liked"], body["rank"]), (1, True, 1))

    def test_similar(self):
        games = self.client.get("/api/games/dnfm/similar?limit=5").json()["games"]
        self.assertEqual(len(games), 5)
        self.assertNotIn("dnfm", [g["id"] for g in games])
        self.assertEqual(games[0]["id"], "maplem")
        self.assertIn("similarity", games[0])

    def test_similar_blends_my_taste(self):
        # C7-e: 섞은 점수(유사도 × 0.6 + 취향 × 0.4) 순, 지금 게임·비선호 태그 게임 제외
        self.survey({"genres": ["RPG"], "subgenres": ["턴제RPG"], "dislikes": ["bm:가챠"]})
        games = self.client.get("/api/games/dnfm/similar?limit=12").json()["games"]
        self.assertTrue(games)
        self.assertNotIn("dnfm", [g["id"] for g in games])
        self.assertTrue(all("가챠" not in g["tags"] for g in games))
        blend = [g["similarity"] * 0.6 + g["match"] * 0.4 for g in games]
        self.assertEqual(blend, sorted(blend, reverse=True))
        self.assertEqual(set(games[0]), {"id", "name", "genre", "tags", "tier", "indie", "thumbnail_url",
                                         "match", "similarity"})

    def test_reviews_not_ready_then_temp(self):
        self.assertEqual(self.client.get("/api/games/dq8/reviews").json(),
                         {"ready": False, "source": None, "stars": [], "topics": []})
        conn = self.db()
        conn.execute("INSERT INTO review_stats VALUES ('dq8', 5, 10, 8, 2, 1, 0, 'temp')")
        conn.execute("INSERT INTO review_topics VALUES ('dq8', '과금', 3, 0.3, 'temp')")
        conn.commit()
        body = self.client.get("/api/games/dq8/reviews").json()
        self.assertEqual((body["ready"], body["source"]), (True, "temp"))
        self.assertEqual(body["stars"][0]["review_count"], 10)
        self.assertEqual(body["topics"][0]["topic"], "과금")


class ReactionApiTest(ApiTestCase):
    def react(self, gid, type_):
        return self.client.post("/api/reactions", json={"game_id": gid, "type": type_})

    def test_like_once_and_cancel(self):
        self.assertEqual(self.react("dq8", "like").json(), {"applied": True, "like_count": 1, "liked": True})
        self.assertEqual(self.react("dq8", "like").json(), {"applied": False, "like_count": 1, "liked": True})
        r = self.client.delete("/api/reactions/like/dq8")
        self.assertEqual(r.json(), {"cancelled": True, "like_count": 0, "liked": False})
        self.assertFalse(self.client.delete("/api/reactions/like/dq8").json()["cancelled"])

    def test_store_once_per_day(self):
        self.assertTrue(self.react("dq8", "store").json()["applied"])
        self.assertFalse(self.react("dq8", "store").json()["applied"])

    def test_unknown_game_and_view_rejected(self):
        self.assertEqual(self.react("nope", "like").status_code, 404)
        self.assertEqual(self.react("dq8", "view").status_code, 422)

    def test_rate_limit(self):
        limit = RATE_LIMITS["reaction"]
        for _ in range(limit):
            self.assertEqual(self.react("dq8", "dislike").status_code, 200)
        r = self.react("dq8", "dislike")
        self.assertEqual(r.status_code, 429)
        self.assertIn("error", r.json())
        self.assertEqual(r.headers["retry-after"], "60")


class SearchTest(ApiTestCase):
    def search(self, **params):
        r = self.client.get("/api/search", params=params)
        self.assertEqual(r.status_code, 200, r.text)
        return [g["id"] for g in r.json()["games"]]

    def test_name_and_tag_partial_match(self):
        self.assertIn("maplem", self.search(q="메이플"))
        self.assertIn("dq8", self.search(q="턴제"))
        self.assertEqual(self.search(q="없는게임이름"), [])
        self.assertEqual(self.search(), [])

    def test_tag_filter_requires_all(self):
        ids = self.search(tags="RPG,2D")
        self.assertIn("dnfm", ids)
        self.assertNotIn("dq8", ids)  # 3D

    def test_logs_keyword_and_popular(self):
        for q in ("메이플", "메이플", "  RPG "):
            self.search(q=q)
        self.search(tags="2D")  # 태그만 고른 검색은 기록 안 함
        keywords = self.client.get("/api/search/popular").json()["keywords"]
        self.assertEqual(keywords[:2], [{"keyword": "메이플", "count": 2, "source": "search"},
                                        {"keyword": "RPG", "count": 1, "source": "search"}])
        # 모자라면 게임이 많은 태그로 10개까지 채움 (이미 나온 말은 빼고)
        self.assertEqual(len(keywords), 10)
        self.assertTrue(all(k["source"] == "tag" for k in keywords[2:]))
        self.assertEqual(len({k["keyword"] for k in keywords}), 10)
        self.assertEqual(self.db().execute("PRAGMA table_info(search_logs)").fetchall()[1]["name"], "keyword")

    def test_popular_without_logs_is_tags(self):
        keywords = self.client.get("/api/search/popular").json()["keywords"]
        self.assertEqual(len(keywords), 10)
        self.assertTrue(all(k["source"] == "tag" for k in keywords))

    # ---------- C7-c: 띄어쓰기로 여러 키워드 (AND) ----------
    def games_by_id(self):
        return {g["id"]: g for g in json.loads(init_db.GAMES_JSON.read_text(encoding="utf-8"))}

    def test_free_2d(self):
        r = self.client.get("/api/search", params={"q": "무료 2D"}).json()
        ids = [g["id"] for g in r["games"]]
        games = self.games_by_id()
        expected = {gid for gid, g in games.items() if "2D" in g["view"] and "유료구매" not in g["bm"]}
        self.assertTrue(expected)
        self.assertEqual(set(ids), expected)
        self.assertEqual([(k["word"], k["kind"]) for k in r["keywords"]], [("무료", "synonym"), ("2D", "tag")])
        self.assertEqual(r["keywords"][0]["label"], "유료구매 제외")

    def test_rpg_pixel_art(self):
        ids = self.search(q="RPG 픽셀아트")
        games = self.games_by_id()
        expected = {gid for gid, g in games.items() if g["genre"] == "RPG" and "픽셀아트" in g["view"]}
        self.assertTrue(expected)
        self.assertEqual(set(ids), expected)

    def test_exact_game_name_first(self):
        r = self.client.get("/api/search", params={"q": "쿠키런 킹덤"}).json()
        self.assertEqual(r["games"][0]["id"], "cookiekingdom")
        self.assertTrue(r["games"][0]["exact"])
        self.assertEqual(set(self.search(q="쿠키런")), {"cookiekingdom", "cookierun"})  # 한 단어면 둘 다

    def test_words_are_and(self):
        self.assertEqual(self.search(q="RPG 퍼즐"), [])                 # 장르가 둘 다인 게임 없음
        self.assertEqual(self.search(q="2d"), self.search(q="2D"))       # 태그 이름은 대소문자 무시
        self.assertEqual(set(self.search(q="혼자 도트")), set(self.search(q="싱글 픽셀아트")))  # 동의어

    def test_free_excludes_paid(self):
        games = self.games_by_id()
        for gid in self.search(q="무료"):
            self.assertNotIn("유료구매", games[gid]["bm"])

    def test_suggest_prefix_max_8(self):
        items = self.client.get("/api/search/suggest", params={"q": "메"}).json()["suggestions"]
        self.assertIn({"type": "game", "text": "메이플스토리M", "id": "maplem"}, items)
        self.assertTrue(all(i["text"].startswith("메") for i in items))
        self.assertLessEqual(len(self.client.get("/api/search/suggest", params={"q": "M"}).json()["suggestions"]), 8)
        self.assertEqual(self.client.get("/api/search/suggest", params={"q": ""}).status_code, 422)

    def test_input_limits(self):
        self.assertEqual(self.client.get("/api/search", params={"q": "가" * 31}).status_code, 422)  # 한 단어 30자
        self.assertEqual(self.client.get("/api/search", params={"q": "가 " * 11}).status_code, 422)  # 10단어
        self.assertEqual(self.client.get("/api/search", params={"q": "가" * 61}).status_code, 422)  # 전체 60자
        self.assertEqual(self.client.get("/api/search", params={"tags": ",".join("abcdefghijk")}).status_code, 422)

    def test_search_rate_limit(self):
        for _ in range(RATE_LIMITS["search"]):
            self.client.get("/api/search", params={"q": "a"})
        self.assertEqual(self.client.get("/api/search", params={"q": "a"}).status_code, 429)

    def test_tags_list(self):
        tags = self.client.get("/api/tags").json()["tags"]
        self.assertEqual(list(tags), ["genre", "subgenre", "view", "play", "mood", "bm"])
        self.assertEqual(sum(len(v) for v in tags.values()), 59)
        rpg = next(t for t in tags["genre"] if t["name"] == "RPG")
        self.assertGreater(rpg["game_count"], 0)


class MeTest(ApiTestCase):
    def nickname(self, value):
        return self.client.patch("/api/me", json={"nickname": value})

    def test_nickname_rules(self):
        for ok in ("게임왕", "가나", "가나다라마바사아자차", "gamer", "abcd", "abcdefghijkl", "게임king",
                   "게임왕123", "  게임왕  "):
            r = self.nickname(ok)
            self.assertEqual(r.status_code, 200, (ok, r.text))
        self.assertEqual(self.client.get("/api/me").json()["nickname"], "게임왕")
        for bad in ("가", "가나다라마바사아자차카", "abc", "abcdefghijklm", "게임king1234567", "게임 왕", "abc!",
                    "1234", "<b>hi</b>", ""):
            r = self.nickname(bad)
            self.assertEqual(r.status_code, 422, bad)
            self.assertIn("error", r.json())

    def test_taste_axes(self):
        self.survey({"genres": ["RPG"], "dislikes": ["bm:가챠"]})
        body = self.client.get("/api/me/taste").json()
        axes = {a["category"]: a["average"] for a in body["axes"]}
        self.assertEqual(list(axes), ["genre", "subgenre", "view", "play", "mood", "bm"])
        self.assertEqual(axes["genre"], 2.0)
        self.assertEqual(axes["bm"], -5.0)
        self.assertEqual(body["top"][0]["name"], "RPG")
        self.assertEqual(body["bottom"][0]["name"], "가챠")


class SettingsTest(ApiTestCase):
    def test_disliked_tags(self):
        self.survey({"genres": ["RPG"]})
        r = self.client.put("/api/me/disliked-tags", json={"tags": ["bm:가챠", "bm:가챠"]})
        self.assertEqual(r.json(), {"tags": ["bm:가챠"]})
        games = self.client.get("/api/feed?limit=30").json()["games"]
        self.assertTrue(all("가챠" not in g["tags"] for g in games))
        self.assertEqual(self.client.put("/api/me/disliked-tags", json={"tags": ["bm:없음"]}).status_code, 422)
        self.assertEqual(self.client.put("/api/me/disliked-tags", json={"tags": ["가챠"]}).status_code, 422)

    def test_reset_keeps_views(self):
        self.survey({"genres": ["RPG"]})
        self.client.get("/api/games/dq8")
        for type_ in ("like", "store", "dislike"):
            self.client.post("/api/reactions", json={"game_id": "ff6", "type": type_})
        self.assertEqual(self.client.post("/api/me/reset").json(), {"deleted_reactions": 3})
        rows = self.db().execute("SELECT type FROM reactions").fetchall()
        self.assertEqual([r["type"] for r in rows], ["view"])
        self.assertEqual(self.client.get("/api/me/taste").json()["axes"][0]["average"], 2.0)

    def test_export(self):
        self.survey({"genres": ["RPG"]})
        self.client.post("/api/reactions", json={"game_id": "dq8", "type": "like"})
        r = self.client.get("/api/me/export")
        self.assertIn("attachment", r.headers["content-disposition"])
        body = r.json()
        self.assertEqual(body["survey"]["genres"], ["RPG"])
        self.assertEqual(body["reactions"][0]["type"], "like")
        self.assertEqual(body["user"]["id"], self.uid())

    def test_delete_me(self):
        self.survey({"genres": ["RPG"]})
        self.client.post("/api/reactions", json={"game_id": "dq8", "type": "like"})
        uid = self.uid()
        r = self.client.delete("/api/me")
        self.assertEqual(r.json(), {"deleted": True})
        self.assertIn(f'{COOKIE_NAME}=""', r.headers["set-cookie"])
        conn = self.db()
        for table in ("users", "survey_answers", "user_tag_scores", "reactions"):
            column = "id" if table == "users" else "user_id"
            self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (uid,)).fetchone()[0], 0)
        self.client.get("/api/me")
        self.assertNotEqual(self.uid(), uid)


if __name__ == "__main__":
    unittest.main()

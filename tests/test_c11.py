"""
C11 배포 후 첫 수정 묶음 (docs/C11_지시.md, PROJECT.md 4.11 C11).
화면 계산(필터·설문 대표 보기)은 떼어 둔 JS 모듈을 node로 직접 실행해 확인 (node가 없으면 그 테스트만 건너뜀).
실행: python -m unittest discover tests   (프로젝트 최상위에서)
"""
import json
import re
import shutil
import subprocess
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, services  # noqa: E402
from app.db import get_connection  # noqa: E402
from tests.test_api import ApiTestCase  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
JS = BASE / "static" / "js"
NODE = shutil.which("node")
TODAY = date.today()


def run_node(module: str, expression: str):
    """static/js/<module>을 불러 expression 결과를 JSON으로"""
    script = (f"import * as m from {json.dumps((JS / module).as_uri())};\n"
              f"console.log(JSON.stringify({expression}));")
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=30, check=True)
    return json.loads(out.stdout)


# ---------- C11-a ----------
class DeployFixTest(unittest.TestCase):
    def test_requirements_security_floor(self):
        text = (BASE / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("anyio>=4.14.2", text)
        self.assertIn("certifi>=2026.10.18", text)

    def test_no_gamepic_typo_on_screen(self):
        """C11-b: 화면 영문 표기는 GamePick / GAMEPICK (GAMEPIC 오타 없음)"""
        for path in list((BASE / "static" / "js").glob("*.js")) + list((BASE / "static" / "pages").glob("*.html")):
            self.assertIsNone(re.search(r"GAMEPIC(?!K)|GamePic(?!k)", path.read_text(encoding="utf-8")), path.name)


# ---------- C11-c 랭킹 ----------
class RankingSummaryTest(ApiTestCase):
    def test_first_sentence(self):
        f = services.first_sentence
        self.assertEqual(f("당신의 시간!  어디서나 액션쾌감! 던전"), "당신의 시간!")
        self.assertEqual(f("v1.2 업데이트. 다음 문장"), "v1.2 업데이트.")   # 숫자 사이 점은 문장 끝 아님
        self.assertEqual(f("\n  첫 줄\n둘째 줄"), "첫 줄")
        self.assertIsNone(f(None))
        self.assertIsNone(f("   "))
        self.assertEqual(len(f("가" * 300)), services.SUMMARY_MAX)

    def test_ranking_has_summary_and_thumbnail(self):
        conn = self.db()
        conn.execute("UPDATE games SET description = '첫 문장입니다. 둘째 문장' WHERE id = 'dq8'")
        conn.execute("UPDATE games SET description = NULL WHERE id = 'ff6'")
        conn.commit()
        for game in ("dq8", "ff6"):
            self.client.post("/api/reactions", json={"game_id": game, "type": "like"})
        ranking = {g["id"]: g for g in self.client.get("/api/ranking").json()["ranking"]}
        self.assertEqual(ranking["dq8"]["summary"], "첫 문장입니다.")
        self.assertIsNone(ranking["ff6"]["summary"])                 # 화면이 장르·태그로 대신
        self.assertIn("thumbnail_url", ranking["dq8"])
        self.assertEqual(ranking["dq8"]["like_count"], 1)            # 기존 필드 그대로


# ---------- C11-d 신작 ----------
class NewGamesTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        conn = self.db()
        conn.execute("UPDATE games SET release_date = NULL")
        dates = {"dq8": TODAY.isoformat(), "ff6": f"{TODAY.year}-01-01", "chrono": f"{TODAY.year - 1}-06-01",
                 "dnfm": "2018-03-02", "maplem": (TODAY + timedelta(days=10)).isoformat()}   # maplem = 출시 예정
        for game_id, day in dates.items():
            conn.execute("UPDATE games SET release_date = ? WHERE id = ?", (day, game_id))
        conn.commit()

    def test_years_order_and_prerelease(self):
        body = self.client.get("/api/new-games").json()
        self.assertEqual(body["years"], [TODAY.year, TODAY.year - 1])
        ids = [g["id"] for g in body["games"]]
        self.assertEqual(ids[:4], ["dq8", "ff6", "chrono", "dnfm"])   # 최신순, 날짜 있는 게임 먼저
        self.assertNotIn("maplem", ids)                                # 출시 예정 제외
        self.assertIn("genshin", ids)                                  # 날짜 없는 게임은 맨 뒤 (그 밖의 게임)
        self.assertGreater(ids.index("genshin"), ids.index("dnfm"))
        self.assertEqual(body["games"][0]["release_date"], TODAY.isoformat())

    def test_filters_only_used_tags(self):
        filters = self.client.get("/api/new-games").json()["filters"]
        self.assertIn("RPG", filters["genre"])
        self.assertEqual(set(filters["keyword"]), {"subgenre", "play", "mood", "view", "bm"})
        self.assertIn("턴제RPG", filters["keyword"]["subgenre"])
        self.assertNotIn("턴제RPG", filters["genre"])


@unittest.skipUnless(NODE, "node가 없어 화면 계산 확인을 건너뜀")
class NewFilterJsTest(unittest.TestCase):
    def test_genre_or_keyword_and(self):
        games = [{"genre": "RPG", "tags": ["턴제RPG", "싱글"]}, {"genre": "퍼즐", "tags": ["싱글"]},
                 {"genre": "액션", "tags": ["싱글", "PvP"]}]
        cases = [([], []), (["RPG", "퍼즐"], []), ([], ["싱글", "PvP"]), (["RPG", "퍼즐"], ["싱글"]), (["RPG"], ["PvP"])]
        expr = (f"{json.dumps(cases)}.map(([g, k]) => {json.dumps(games)}"
                ".map((x) => m.matchesFilter(x, new Set(g), new Set(k))))")
        self.assertEqual(run_node("filters.js", expr), [
            [True, True, True],      # 필터 없음
            [True, True, False],     # 장르 OR
            [False, False, True],    # 키워드 AND
            [True, True, False],     # 장르 AND 키워드
            [False, False, False],
        ])

    GAMES = [  # API 순서 그대로 (최신순, 날짜 없는 게임 맨 뒤)
        {"id": "a", "release_date": "2026-11-01", "genre": "RPG", "tags": ["싱글"]},
        {"id": "b", "release_date": "2026-02-01", "genre": "퍼즐", "tags": []},
        {"id": "c", "release_date": "2025-06-01", "genre": "RPG", "tags": ["싱글"]},
        {"id": "d", "release_date": "2018-03-02", "genre": "RPG", "tags": []},
        {"id": "e", "release_date": "2024-01-01", "genre": "RPG", "tags": ["싱글"]},
        {"id": "f", "release_date": None, "genre": "RPG", "tags": ["싱글"]},
    ]

    def groups(self, all_, genres=(), keywords=()):
        expr = (f"m.yearGroups({json.dumps(self.GAMES)}.filter((g) => m.matchesFilter(g, new Set({json.dumps(list(genres))}), "
                f"new Set({json.dumps(list(keywords))}))), 2026, {json.dumps(all_)}).map(([t, l]) => [t, l.map((g) => g.id)])")
        return run_node("filters.js", expr)

    def test_default_is_this_year_only(self):
        """C11-h: 기본 화면 = 2026년작 한 칸 (2025년작·그 밖 칸 없음)"""
        self.assertEqual(self.groups(False), [["2026년작", ["a", "b"]]])

    def test_all_view_years_desc_then_no_date(self):
        self.assertEqual(self.groups(True), [["2026년작", ["a", "b"]], ["2025년작", ["c"]], ["2024년작", ["e"]],
                                             ["2018년작", ["d"]], ["출시일 없음", ["f"]]])

    def test_filter_applies_to_both_views(self):
        self.assertEqual(self.groups(False, ["RPG"], ["싱글"]), [["2026년작", ["a"]]])
        self.assertEqual(self.groups(True, ["RPG"], ["싱글"]),
                         [["2026년작", ["a"]], ["2025년작", ["c"]], ["2024년작", ["e"]], ["출시일 없음", ["f"]]])
        self.assertEqual(self.groups(False, ["슈팅"]), [])   # 빈 칸은 빠짐 → 화면은 "조건에 맞는 … 없어요"


# ---------- C11-e 비선호 연관 태그 ----------
class DislikeRelatedTest(ApiTestCase):
    def test_table_and_expand(self):
        table = services.dislike_related()
        self.assertIn("mood:귀여움", table["genre:캐주얼"])
        self.assertEqual(set(table["genre:퍼즐"]), {"subgenre:매치3", "subgenre:감성퍼즐"})
        tags = {f"{r['category']}:{r['name']}" for r in self.db().execute("SELECT category, name FROM tags")}
        for key, related in table.items():                            # 표의 태그는 모두 실제 태그
            self.assertLessEqual({key, *related}, tags, key)
        self.assertEqual(services.expand_dislikes({"play:PvP"}), {"play:PvP"})
        self.assertIn("subgenre:러너", services.expand_dislikes({"genre:캐주얼"}))

    def test_missing_file_means_no_expansion(self):
        with mock.patch.object(config, "DISLIKE_RELATED_PATH", Path(self.tmp.name) / "none.json"):
            self.assertEqual(services.expand_dislikes({"genre:캐주얼"}), {"genre:캐주얼"})

    def cute_rpg_user(self):
        """RPG를 좋아하고 캐주얼을 싫어함 → 귀여움 RPG(메이플·블루 아카이브 …)도 빠져야 함 (C11-e 증상)"""
        self.survey({"genres": ["RPG"], "dislikes": ["genre:캐주얼"]})

    def test_feed_excludes_related(self):
        self.cute_rpg_user()
        ids = {g["id"] for g in self.client.get("/api/feed?section=match&limit=30").json()["games"]}
        self.assertTrue(ids)
        self.assertFalse(ids & {"maplem", "bluearchive", "7kidle", "mushroom", "cookiekingdom"})

    def test_existing_user_applies_without_resurvey(self):
        """저장된 답은 그대로 두고 표만 읽음 → 표를 바꾸면 다음 요청부터 바로 반영"""
        self.cute_rpg_user()
        with mock.patch.object(services, "expand_dislikes", lambda f: set(f)):
            before = {g["id"] for g in self.client.get("/api/feed?section=match&limit=30").json()["games"]}
        self.assertTrue(before & {"maplem", "bluearchive", "7kidle", "mushroom", "cookiekingdom"})

    def test_similar_excludes_related(self):
        self.cute_rpg_user()
        ids = {g["id"] for g in self.client.get("/api/games/dq8/similar?limit=12").json()["games"]}
        self.assertFalse(ids & {"maplem", "bluearchive", "brawl", "cookierun"})

    def test_search_excludes_but_keeps_exact_and_searched_tag(self):
        self.cute_rpg_user()
        names = lambda **p: [g["id"] for g in self.client.get("/api/search", params=p).json()["games"]]  # noqa: E731
        # C12-k부터: 검색어가 이름에 들어 있으면 비선호여도 남김 (전에는 둘 다 빠졌음)
        self.assertEqual(set(names(q="쿠키런")), {"cookierun", "cookiekingdom"})
        self.assertEqual(names(q="쿠키런: 오븐브레이크"), ["cookierun"])           # 이름을 정확히 치면 남김
        cute = names(tags="귀여움")                                               # 귀여움을 직접 찾으면
        self.assertIn("maplem", cute)                                            # 귀여움 때문에 빠지던 건 보임
        self.assertNotIn("cookierun", cute)                                      # 캐주얼 자체는 여전히 빠짐


# ---------- C11-f 설문 ----------
class SurveyQuestionsTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.use_real_survey()
        self.qs = {q["id"]: q for q in self.client.get("/api/survey/questions").json()["questions"]}

    def test_featured_from_survey_json(self):
        for qid in ("q3", "q4"):
            featured = self.qs[qid]["featured"]
            self.assertEqual(len(featured["genre"]), 5)
            self.assertEqual(len(featured["keyword"]), 5)
            values = {o["value"] for o in self.qs[qid]["options"]}
            self.assertLessEqual(set(featured["genre"] + featured["keyword"]), values)
        self.assertEqual(self.qs["q3"]["featured"]["keyword"],
                         ["mood:힐링", "mood:귀여움", "mood:판타지", "mood:스토리중심", "play:PvP"])

    def test_featured_drops_unknown_values(self):
        data = json.loads(self.survey_path.read_text(encoding="utf-8"))
        data["questions"][3]["featured"]["genre"].append("genre:없는장르")
        self.survey_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        q3 = next(q for q in self.client.get("/api/survey/questions").json()["questions"] if q["id"] == "q3")
        self.assertNotIn("genre:없는장르", q3["featured"]["genre"])

    def test_aliases_from_synonyms(self):
        single = next(o for o in self.qs["q4"]["options"] if o["value"] == "play:싱글")
        self.assertIn("혼자", single["aliases"])

    def test_q3_keyword_answer_saves(self):
        r = self.answer({"q1": ["ios"], "q2": ["any"], "q3": ["genre:RPG", "mood:힐링"], "q4": ["none"], "q5": []})
        self.assertEqual(r.status_code, 200)

    def test_q5_all_games_with_ranking_fields(self):
        conn = self.db()
        conn.execute("UPDATE games SET source = 'auto' WHERE id = 'genshin'")   # 자동 수집 게임도 보기에 (검색용)
        conn.commit()
        qs = {q["id"]: q for q in self.client.get("/api/survey/questions").json()["questions"]}
        games = {o["value"]: o for o in qs["q5"]["options"] if not o.get("exclusive")}
        self.assertIn("genshin", games)
        self.assertIn("genre:RPG", games["dq8"]["features"])
        self.assertEqual({k: type(games["dq8"][k]) for k in ("likes", "installs")}, {"likes": int, "installs": int})
        self.assertNotIn("engine", games["dq8"])


@unittest.skipUnless(NODE, "node가 없어 화면 계산 확인을 건너뜀")
class SurveyPickJsTest(unittest.TestCase):
    def test_option_matches_label_and_alias(self):
        options = [{"label": "로그라이크RPG"}, {"label": "싱글", "aliases": ["혼자", "솔로"]}, {"label": "Royal Match"}]
        expr = (f"[\"로그\", \"혼자\", \"royalm\", \" \", \"없음\"].map((q) => {json.dumps(options)}"
                ".filter((o) => m.optionMatches(o, q)).map((o) => o.label))")
        self.assertEqual(run_node("survey_pick.js", expr),
                         [["로그라이크RPG"], ["싱글"], ["Royal Match"], [], []])

    def test_featured_games_order(self):
        options = [
            {"value": "a", "features": ["genre:퍼즐"], "likes": 9, "installs": 9},
            {"value": "b", "features": ["genre:RPG", "subgenre:턴제RPG"], "likes": 0, "installs": 1},
            {"value": "c", "features": ["genre:RPG"], "likes": 2, "installs": 0},
            {"value": "d", "features": ["genre:RPG"], "likes": 2, "installs": 5},
        ]
        picked = ["genre:RPG", "subgenre:턴제RPG"]
        self.assertEqual(run_node("survey_pick.js",
                                  f"m.featuredGames({json.dumps(options)}, {json.dumps(picked)}).map((o) => o.value)"),
                         ["b", "d", "c"])   # 겹침 수 → 추천 수 → 설치 수
        self.assertEqual(run_node("survey_pick.js", f"m.featuredGames({json.dumps(options)}, []).map((o) => o.value)"),
                         ["a", "d", "c"])   # 3번 답이 없으면 추천 수 → 설치 수 순


if __name__ == "__main__":
    unittest.main()

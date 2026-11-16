"""
C8 테스트 (docs/C8_지시.md): 홈 60%·태그 3개 이하 제외, 신작 정렬·offset, 상세 댓글.
iOS만 넣기 규칙·검수 대기 다시 판정은 test_sync_store.py에.
실행: python -m unittest discover tests   (프로젝트 최상위에서)
"""
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import security  # noqa: E402
from app.main import app  # noqa: E402
from tests.test_api import ApiTestCase  # noqa: E402

TODAY = date.today()


class HomeSliderTest(ApiTestCase):
    def add_two_tag_game(self):
        """취향(RPG·턴제RPG)과 딱 맞는 태그 2개짜리 게임"""
        conn = self.db()
        conn.execute("INSERT INTO games (id, name, source) VALUES ('twotag', '두 태그 RPG', 'auto')")
        conn.execute("INSERT INTO game_tags (game_id, tag_id, auto) SELECT 'twotag', id, 1 FROM tags "
                     "WHERE (category = 'genre' AND name = 'RPG') OR (category = 'subgenre' AND name = '턴제RPG')")
        conn.commit()

    def test_home_main_slots_need_4_tags(self):
        self.add_two_tag_game()
        self.new_user("feedback")
        self.survey({"genres": ["RPG"], "subgenres": ["턴제RPG"], "favorites": ["dq8"]})
        plain = self.client.get("/api/feed?section=match&limit=30").json()["games"]
        self.assertIn("twotag", [g["id"] for g in plain])                       # 기준 없으면 나옴
        body = self.client.get("/api/feed?section=match&limit=9&min_match=60").json()
        self.assertFalse(body["fallback"])
        main = [g for g in body["games"] if g["slot"] == "추천"]
        self.assertTrue(main)
        self.assertTrue(all(g["match"] >= 60 and len(g["tags"]) + 1 >= 4 for g in main))  # tags = 장르 뺀 태그
        self.assertNotIn("twotag", [g["id"] for g in main])

    def test_random_group_also_needs_4_tags(self):
        self.add_two_tag_game()
        self.new_user("random")
        self.survey({"genres": ["RPG"], "subgenres": ["턴제RPG"], "favorites": ["dq8"]})
        body = self.client.get("/api/feed?section=match&limit=9&min_match=60").json()
        self.assertFalse(body["fallback"])
        self.assertNotIn("twotag", [g["id"] for g in body["games"]])


class NewTabTest(ApiTestCase):
    def set_dates(self):
        conn = self.db()
        rows = {"gris": (1, 0), "dq8": (5, 0), "darkest": (3, 10), "chrono": (2, 0)}  # (며칠 전, 별점 수)
        for gid, (days, ratings) in rows.items():
            conn.execute("UPDATE games SET release_date = ?, store_rating_count = ? WHERE id = ?",
                         ((TODAY - timedelta(days=days)).isoformat(), ratings, gid))
        conn.execute("UPDATE games SET name = 'Darkest Dungeon' WHERE id = 'darkest'")  # 한글 없음, 별점 10개
        conn.commit()

    def test_familiar_games_first_then_newest(self):
        self.set_dates()
        ids = [g["id"] for g in self.client.get("/api/feed?section=new").json()["games"]]
        # 한글 이름이거나 별점 있는 게임(크로노 트리거 2일 전, Darkest 3일 전, 드래곤 퀘스트 5일 전) → GRIS(한글 없음·별점 0, 1일 전)
        self.assertEqual(ids, ["chrono", "darkest", "dq8", "gris"])

    def test_offset_and_has_more(self):
        self.set_dates()
        first = self.client.get("/api/feed?section=new&limit=3").json()
        second = self.client.get("/api/feed?section=new&limit=3&offset=3").json()
        self.assertEqual(([g["id"] for g in first["games"]], first["has_more"]), (["chrono", "darkest", "dq8"], True))
        self.assertEqual(([g["id"] for g in second["games"]], second["has_more"]), (["gris"], False))
        conn = self.db()
        for i, gid in enumerate(("brawl", "candy", "coc"), 1):
            conn.execute("UPDATE games SET chart_rank_android = ? WHERE id = ?", (i, gid))
        conn.commit()
        page = self.client.get("/api/feed?section=popular&limit=2&offset=1").json()
        self.assertEqual(([g["id"] for g in page["games"]], page["has_more"]), (["candy", "coc"], False))
        self.assertEqual(self.client.get("/api/feed?section=new&offset=-1").status_code, 422)


class CommentTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.client.post("/api/users")
        self.url = "/api/games/dq8/comments"

    def post(self, body, client=None, game="dq8"):
        return (client or self.client).post(f"/api/games/{game}/comments", json={"body": body})

    def other(self):
        client = TestClient(app)
        self.addCleanup(client.close)
        client.post("/api/users")
        return client

    def test_create_list_edit_delete(self):
        self.client.patch("/api/me", json={"nickname": "게임왕"})
        r = self.post("  재밌어요\r\n추천!  ")
        self.assertEqual(r.status_code, 201, r.text)
        c = r.json()
        self.assertEqual((c["body"], c["nickname"], c["mine"], c["edited"]), ("재밌어요\n추천!", "게임왕", True, False))
        other = self.other()
        self.post("저도요", client=other)
        page = self.client.get(self.url).json()
        self.assertEqual(page["total"], 2)
        self.assertEqual([(x["body"], x["nickname"], x["mine"]) for x in page["comments"]],
                         [("저도요", "익명 사용자", False), ("재밌어요\n추천!", "게임왕", True)])  # 최신순
        r = self.client.patch(f"/api/comments/{c['id']}", json={"body": "고친 글"})
        self.assertEqual((r.status_code, r.json()["body"], r.json()["edited"]), (200, "고친 글", True))
        self.assertEqual(self.client.delete(f"/api/comments/{c['id']}").status_code, 200)
        self.assertEqual(self.client.get(self.url).json()["total"], 1)
        self.assertIsNone(self.db().execute("SELECT 1 FROM comments WHERE id = ?", (c["id"],)).fetchone())  # 실제 삭제

    def test_length_lines_and_control_chars(self):
        for body in ("", "   ", "\n\n", "가" * 301, "\n".join(["줄"] * 11)):
            r = self.post(body)
            self.assertEqual((r.status_code, list(r.json())), (422, ["error"]), repr(body[:20]))
        security.limiter.reset()
        self.assertEqual(self.post("가" * 300).status_code, 201)
        self.assertEqual(self.post("\n".join(["줄"] * 10)).status_code, 201)
        r = self.post("좋\x00아‮요\x07")
        self.assertEqual(r.json()["body"], "좋아요")
        self.assertEqual(self.client.post(self.url, json={"body": "a", "html": "<b>"}).status_code, 422)

    def test_others_comment_forbidden_and_missing(self):
        cid = self.post("내 글").json()["id"]
        other = self.other()
        self.assertEqual(other.patch(f"/api/comments/{cid}", json={"body": "남의 글 고치기"}).status_code, 403)
        self.assertEqual(other.delete(f"/api/comments/{cid}").status_code, 403)
        self.assertEqual(self.client.patch("/api/comments/99999", json={"body": "x"}).status_code, 404)
        self.assertEqual(self.client.delete("/api/comments/99999").status_code, 404)
        self.assertEqual(self.client.get("/api/games/nope/comments").status_code, 404)
        self.assertEqual(self.post("x", game="nope").status_code, 404)
        self.assertEqual(self.client.get(f"{self.url}?limit=51").status_code, 422)

    def test_pagination_before_id(self):
        with mock.patch.object(security, "COMMENT_PER_MINUTE", 100):
            ids = [self.post(f"댓글 {i}").json()["id"] for i in range(5)]
        first = self.client.get(f"{self.url}?limit=2").json()
        self.assertEqual(([c["id"] for c in first["comments"]], first["has_more"]), (ids[:2:-1][:2], True))
        last = self.client.get(f"{self.url}?limit=2&before_id={ids[1]}").json()
        self.assertEqual(([c["id"] for c in last["comments"]], last["has_more"]), ([ids[0]], False))

    def test_rate_limit_minute_and_day(self):
        codes = [self.post(f"{i}").status_code for i in range(6)]
        self.assertEqual(codes, [201] * 5 + [429])
        cid = self.db().execute("SELECT MAX(id) FROM comments").fetchone()[0]
        self.assertEqual(self.client.patch(f"/api/comments/{cid}", json={"body": "x"}).status_code, 429)  # 고치기도 같이 셈
        security.limiter.reset()
        with mock.patch.object(security, "COMMENT_PER_MINUTE", 100), mock.patch.object(security, "COMMENT_PER_DAY", 2):
            codes = [self.post(f"d{i}").status_code for i in range(3)]
        self.assertEqual(codes, [201, 201, 429])

    def test_user_delete_export_and_reset(self):
        self.post("남길 글")
        export = self.client.get("/api/me/export").json()
        self.assertEqual([c["body"] for c in export["comments"]], ["남길 글"])
        self.client.post("/api/me/reset")                                   # 추천 기록 초기화는 댓글을 안 지움
        self.assertEqual(self.client.get(self.url).json()["total"], 1)
        self.client.delete("/api/me")                                       # 내 데이터 삭제 → 댓글도
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM comments").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()

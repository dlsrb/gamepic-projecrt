"""
C9 테스트 (docs/C9_지시.md): 댓글 추천·비추천.
실행: python -m unittest discover tests   (프로젝트 최상위에서)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from tests.test_api import ApiTestCase  # noqa: E402


class CommentVoteTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.client.post("/api/users")                       # 글쓴이
        self.cid = self.client.post("/api/games/dq8/comments", json={"body": "글쓴이 댓글"}).json()["id"]
        self.voter = self.other()

    def other(self):
        client = TestClient(app)
        self.addCleanup(client.close)
        client.post("/api/users")
        return client

    def vote(self, value, client=None, cid=None):
        return (client or self.voter).put(f"/api/comments/{cid or self.cid}/vote", json={"value": value})

    def listed(self, client):
        return {c["id"]: c for c in client.get("/api/games/dq8/comments").json()["comments"]}[self.cid]

    @staticmethod
    def counts(data):
        return data["likes"], data["dislikes"], data["my_vote"]

    def test_like_cancel_switch(self):
        r = self.vote(1)
        self.assertEqual((r.status_code, self.counts(r.json())), (200, (1, 0, 1)))
        self.assertEqual(self.counts(self.vote(0).json()), (0, 0, 0))     # 취소
        self.vote(1)
        self.assertEqual(self.counts(self.vote(-1).json()), (0, 1, -1))   # 👍 → 👎 바꾸기
        self.assertEqual(self.counts(self.vote(1).json()), (1, 0, 1))     # 👎 → 👍
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM comment_votes").fetchone()[0], 1)

    def test_same_value_twice_is_same(self):
        self.assertEqual(self.counts(self.vote(1).json()), (1, 0, 1))
        self.assertEqual(self.counts(self.vote(1).json()), (1, 0, 1))
        self.assertEqual(self.counts(self.vote(0).json()), (0, 0, 0))
        self.assertEqual(self.counts(self.vote(0).json()), (0, 0, 0))     # 없는 표 취소도 그대로

    def test_own_missing_and_bad_values(self):
        self.assertEqual(self.vote(1, client=self.client).status_code, 403)        # 내 댓글
        self.assertEqual(self.vote(1, cid=99999).status_code, 404)
        for bad in (2, -2, "a", "1", True, None, 1.5):
            self.assertEqual(self.vote(bad).status_code, 422, repr(bad))
        self.assertEqual(self.voter.put(f"/api/comments/{self.cid}/vote", json={}).status_code, 422)
        self.assertEqual(self.voter.put(f"/api/comments/{self.cid}/vote",
                                        json={"value": 1, "user_id": "x"}).status_code, 422)
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM comment_votes").fetchone()[0], 0)

    def test_list_fields_from_each_viewpoint(self):
        third = self.other()
        self.vote(1)
        self.vote(-1, client=third)
        mine = self.listed(self.client)                     # 글쓴이: 숫자만, 내 상태 0
        self.assertEqual((self.counts(mine), mine["mine"]), ((1, 1, 0), True))
        self.assertEqual(self.counts(self.listed(self.voter)), (1, 1, 1))
        self.assertEqual(self.counts(self.listed(third)), (1, 1, -1))
        self.assertNotIn("voters", mine)
        # 표가 없는 댓글은 0·0·0, 새 댓글·고친 댓글 응답에도 같은 필드
        created = self.voter.post("/api/games/dq8/comments", json={"body": "새 댓글"}).json()
        self.assertEqual(self.counts(created), (0, 0, 0))
        edited = self.client.patch(f"/api/comments/{self.cid}", json={"body": "고친 글"}).json()
        self.assertEqual(self.counts(edited), (1, 1, 0))

    def test_comment_delete_and_user_delete_cascade(self):
        self.vote(1)
        self.client.delete(f"/api/comments/{self.cid}")
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM comment_votes").fetchone()[0], 0)
        cid2 = self.client.post("/api/games/dq8/comments", json={"body": "두 번째"}).json()["id"]
        self.vote(-1, cid=cid2)
        self.voter.delete("/api/me")                        # 누른 사람이 내 데이터 삭제
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM comment_votes").fetchone()[0], 0)

    def test_export_reset_and_no_engine_effect(self):
        self.vote(-1)
        before = self.voter.get("/api/me/taste").json()
        export = self.voter.get("/api/me/export").json()
        self.assertEqual([(v["comment_id"], v["value"]) for v in export["comment_votes"]], [(self.cid, -1)])
        self.assertEqual(self.client.get("/api/me/export").json()["comment_votes"], [])  # 글쓴이 쪽엔 남의 표 없음
        self.assertEqual(self.voter.get("/api/me/taste").json(), before)                 # 태그 점수 그대로
        self.voter.post("/api/me/reset")                    # 추천 기록 초기화는 건드리지 않음
        self.assertEqual(self.counts(self.listed(self.voter)), (0, 1, -1))

    def test_rate_limit_30_per_minute(self):
        codes = [self.vote(i % 2).status_code for i in range(31)]
        self.assertEqual(codes, [200] * 30 + [429])
        # 사용자 기준만: 같은 IP(테스트 클라이언트)의 다른 사용자는 계속 누를 수 있음
        self.assertEqual(self.vote(1, client=self.other()).status_code, 200)


if __name__ == "__main__":
    unittest.main()

"""
C9-b 테스트 (docs/C9_지시.md의 C9-b): 댓글 정렬(latest·oldest·top) + cursor 다음 쪽.
실행: python -m unittest discover tests   (프로젝트 최상위에서)
"""
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from tests.test_api import ApiTestCase  # noqa: E402

URL = "/api/games/dq8/comments"


class CommentSortTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.client.post("/api/users")
        self.voters = []
        for _ in range(4):
            client = TestClient(app)
            self.addCleanup(client.close)
            client.post("/api/users")
            self.voters.append(self.uid(client))

    def add_comments(self, likes: list[int], dislikes: list[int] | None = None) -> list[int]:
        """댓글을 DB에 바로 넣고 (요청 제한을 피하려고) i번 댓글에 👍 likes[i]개·👎 dislikes[i]개. id 목록(등록순)"""
        conn = self.db()
        author = self.uid()
        ids = []
        for i, n in enumerate(likes):
            cid = conn.execute("INSERT INTO comments (game_id, user_id, body) VALUES ('dq8', ?, ?)",
                               (author, f"댓글 {i}")).lastrowid
            down = (dislikes or [0] * len(likes))[i]
            for voter in self.voters[:n]:
                conn.execute("INSERT INTO comment_votes (comment_id, user_id, value) VALUES (?, ?, 1)", (cid, voter))
            for voter in self.voters[n:n + down]:
                conn.execute("INSERT INTO comment_votes (comment_id, user_id, value) VALUES (?, ?, -1)", (cid, voter))
            ids.append(cid)
        conn.commit()
        return ids

    def get(self, query=""):
        r = self.client.get(f"{URL}?{query}")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def walk(self, sort, limit):
        """next_cursor로 끝까지 넘기며 id 모으기"""
        ids, cursor, pages = [], None, 0
        while True:
            page = self.get(f"sort={sort}&limit={limit}" + (f"&cursor={cursor}" if cursor else ""))
            ids += [c["id"] for c in page["comments"]]
            pages += 1
            self.assertEqual(page["has_more"], page["next_cursor"] is not None)
            cursor = page["next_cursor"]
            if cursor is None:
                return ids, pages

    def test_three_orders_and_tie(self):
        a, b, c, d, e = self.add_comments([1, 3, 0, 3, 2], [2, 0, 1, 0, 0])
        self.assertEqual([x["id"] for x in self.get()["comments"]], [e, d, c, b, a])            # 기본 = 최신순
        self.assertEqual([x["id"] for x in self.get("sort=latest")["comments"]], [e, d, c, b, a])
        self.assertEqual([x["id"] for x in self.get("sort=oldest")["comments"]], [a, b, c, d, e])
        top = self.get("sort=top")["comments"]
        self.assertEqual([x["id"] for x in top], [d, b, e, a, c])        # 👍 3·3(같으면 최신 d 먼저)·2·1·0, 👎는 상관없음
        self.assertEqual([(x["likes"], x["dislikes"]) for x in top], [(3, 0), (3, 0), (2, 0), (1, 2), (0, 1)])

    def test_cursor_walk_45_no_gaps_or_duplicates(self):
        rng = random.Random(9)
        counts = [rng.choice([0, 0, 1, 2, 3, 4]) for _ in range(45)]
        ids = self.add_comments(counts)
        likes = dict(zip(ids, counts))
        expected = {
            "latest": sorted(ids, reverse=True),
            "oldest": sorted(ids),
            "top": sorted(ids, key=lambda i: (-likes[i], -i)),
        }
        for sort, want in expected.items():
            for limit in (1, 7, 20, 45, 50):
                got, pages = self.walk(sort, limit)
                self.assertEqual(got, want, f"{sort} limit={limit}")
                self.assertEqual(pages, -(-45 // limit), f"{sort} limit={limit}")

    def test_collapsed_top3_and_total(self):
        ids = self.add_comments([0, 2, 1, 4, 0])
        page = self.get("sort=top&limit=3")
        self.assertEqual(([c["id"] for c in page["comments"]], page["total"], page["has_more"], page["sort"]),
                         ([ids[3], ids[1], ids[2]], 5, True, "top"))

    def test_bad_sort_and_cursor_422(self):
        self.add_comments([1, 0, 2])
        bad = ["sort=new", "sort=TOP", "sort=top&cursor=latest.3", "sort=latest&cursor=top.1.3",
               "sort=top&cursor=top.1", "sort=top&cursor=top.-1.3", "sort=oldest&cursor=oldest.0",
               "sort=latest&cursor=latest.abc", "cursor=latest.1%20OR%201=1", "cursor=" + "1" * 65,
               "sort=top&before_id=3", "sort=oldest&before_id=3", "cursor=latest.3&before_id=3"]
        for query in bad:
            r = self.client.get(f"{URL}?{query}")
            self.assertEqual((r.status_code, list(r.json())), (422, ["error"]), query)

    def test_before_id_still_works_for_latest(self):
        ids = self.add_comments([0] * 5)
        page = self.get(f"limit=2&before_id={ids[3]}")
        self.assertEqual(([c["id"] for c in page["comments"]], page["has_more"]), ([ids[2], ids[1]], True))
        self.assertEqual(page["next_cursor"], f"latest.{ids[1]}")
        self.assertEqual([c["id"] for c in self.get(f"cursor={page['next_cursor']}")["comments"]], [ids[0]])


if __name__ == "__main__":
    unittest.main()

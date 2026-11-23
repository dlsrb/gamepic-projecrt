"""
C12-j 2번: Google Play 태그 칩 → 우리 태그 (data/store_tag_map.json). tag_source='auto' 게임에만 더하기 → 'store', checked 아님.
실행: python -m unittest discover tests   (프로젝트 최상위에서)
"""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import services  # noqa: E402
from app.db import get_connection  # noqa: E402
from scripts import init_db, store_tags  # noqa: E402
from scripts import sync_store as sync  # noqa: E402
from tests.test_api import ApiTestCase  # noqa: E402
from tests.test_sync_store import PLAY, FakeStore, SyncTestCase  # noqa: E402

MAP = sync.load_json("store_tag_map.json")
CHIPS = [{"name": "롤플레잉", "id": "GAME_ROLE_PLAYING"}, {"name": "싱글 플레이어", "id": None},
         {"name": "픽셀", "id": None}, {"name": "스타일", "id": None}, {"name": "경쟁형 멀티플레이어", "id": None}]


class ChipMapTest(unittest.TestCase):
    def test_map_uses_only_real_tags(self):
        """대응표의 태그는 모두 태그표(games.json에서 만든 tags)에 있는 '종류:이름' — 없는 태그는 수집 때 조용히 빠지므로 여기서 막음"""
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_db.prepare(conn, json.loads(init_db.GAMES_JSON.read_text(encoding="utf-8")))
        real = {f"{c}:{n}" for c, n in conn.execute("SELECT category, name FROM tags")}
        for chip, features in MAP.items():
            for feature in features:
                self.assertFalse(feature.startswith("genre:"), chip)   # 장르는 안 바꿈
                self.assertIn(feature, real, f"{chip} → {feature}")

    def test_play_chips_and_mapping(self):
        self.assertEqual(sync.play_chips({"categories": CHIPS}), ["싱글 플레이어", "픽셀", "스타일", "경쟁형 멀티플레이어"])
        self.assertEqual(sync.play_chips({}), [])
        self.assertEqual(sync.chip_tags(["싱글 플레이어", "픽셀", "스타일", "경쟁형 멀티플레이어"], MAP),
                         ["play:싱글", "view:픽셀아트", "play:멀티", "play:PvP"])   # 스타일은 버림
        self.assertEqual(sync.chip_tags(["판타지", "중세 판타지"], MAP), ["mood:판타지"])  # 중복 없이

    def test_ios_has_no_chips(self):
        info = {"trackId": 1, "trackName": "x", "genres": ["게임", "퍼즐"], "primaryGenreId": 6014}
        self.assertEqual(sync.from_ios(info)["store_chips"], [])


class ChipStore(FakeStore):
    def play_details(self, package):
        info = super().play_details(package)
        if package == "com.hero.idle" and info:
            return {**info, "categories": CHIPS}
        return info


class SyncChipsTest(SyncTestCase):
    def test_new_game_with_chips_is_store(self):
        self.run_sync(ChipStore())
        with get_connection(self.db_path) as conn:
            hero = conn.execute("SELECT id, tag_source, tags_checked FROM games WHERE android_id = 'com.hero.idle'").fetchone()
            puzzle = conn.execute("SELECT tag_source FROM games WHERE android_id = 'com.new.puzzle'").fetchone()
            tags = {f"{r['category']}:{r['name']}" for r in conn.execute(
                "SELECT t.category, t.name FROM game_tags gt JOIN tags t ON t.id = gt.tag_id WHERE gt.game_id = ?",
                (hero["id"],))}
        self.assertEqual((hero["tag_source"], hero["tags_checked"]), ("store", 0))   # checked 아님
        self.assertTrue({"play:싱글", "view:픽셀아트", "play:PvP"} <= tags)
        self.assertIn("genre:RPG", tags)                                            # 장르는 스토어 장르 규칙 그대로
        self.assertEqual(puzzle["tag_source"], "auto")                               # 칩 없으면 auto
        self.assertIn("com.hero.idle", PLAY)


class FakePlay:
    def __init__(self, chips_by_package):
        self.chips = chips_by_package
        self.calls = []

    def play_details(self, package):
        self.calls.append(package)
        if package == "broken":
            raise ConnectionError("요청 실패")
        return {"categories": [{"name": c, "id": None} for c in self.chips.get(package, [])]}


class BackfillStoreTagsTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        conn = self.db()
        conn.execute("UPDATE games SET android_id = NULL")
        rows = {"genshin": ("auto", "com.genshin"), "odin": ("auto", "com.odin"), "epic7": ("ai", "com.epic7"),
                "dq8": ("human", "com.dq8"), "hsr": ("auto", "broken"), "lineagem": ("auto", "com.lineagem")}
        for game_id, (tag_source, package) in rows.items():
            conn.execute("UPDATE games SET source = CASE WHEN ? = 'human' THEN 'manual' ELSE 'auto' END, "
                         "tag_source = ?, tags_checked = ?, android_id = ? WHERE id = ?",
                         (tag_source, tag_source, 1 if tag_source in ("ai", "human") else 0, package, game_id))
        conn.commit()
        chip = ["싱글 플레이어", "픽셀", "스타일"]
        self.client_ = FakePlay({"com.genshin": chip, "com.odin": ["스타일"], "com.epic7": chip, "com.dq8": chip})

    def tags(self, game_id):
        return {f"{r[0]}:{r[1]}" for r in self.db().execute(
            "SELECT t.category, t.name FROM game_tags gt JOIN tags t ON t.id = gt.tag_id WHERE gt.game_id = ?", (game_id,))}

    def source(self, game_id):
        return self.db().execute("SELECT tag_source, tags_checked FROM games WHERE id = ?", (game_id,)).fetchone()

    def test_only_auto_games_add_only(self):
        before = {g: self.tags(g) for g in ("genshin", "odin", "epic7", "dq8", "lineagem")}
        with get_connection(self.db_path) as conn:
            report = store_tags.apply_store_tags(conn, self.client_, apply=True)
        self.assertEqual(sorted(self.client_.calls), ["broken", "com.genshin", "com.lineagem", "com.odin"])  # ai·human은 요청도 안 함
        self.assertEqual(report["targets"], 4)
        self.assertEqual(report["failed"], ["hsr"])
        self.assertEqual(report["marked"], ["genshin"])
        self.assertEqual(tuple(self.source("genshin")), ("store", 0))                 # store, checked 아님
        self.assertTrue(before["genshin"] <= self.tags("genshin"))                     # 지운 태그 없음
        self.assertTrue({"play:싱글", "view:픽셀아트"} <= self.tags("genshin"))
        self.assertEqual(tuple(self.source("odin")), ("auto", 0))                       # 대응 없는 칩만 → auto 그대로
        self.assertEqual(tuple(self.source("lineagem")), ("auto", 0))                   # 칩 없음
        for game_id in ("epic7", "dq8"):                                                # ai·human은 안 건드림
            self.assertEqual(self.tags(game_id), before[game_id])
        self.assertEqual(self.source("epic7")[0], "ai")
        self.assertEqual(self.source("dq8")[0], "human")

    def test_dry_run_changes_nothing(self):
        before = self.tags("genshin")
        with get_connection(self.db_path) as conn:
            report = store_tags.apply_store_tags(conn, self.client_)
        self.assertEqual(report["marked"], ["genshin"])
        self.assertEqual(self.tags("genshin"), before)
        self.assertEqual(self.source("genshin")[0], "auto")

    def test_store_games_not_in_checked_pool(self):
        with get_connection(self.db_path) as conn:
            store_tags.apply_store_tags(conn, self.client_, apply=True)
        catalog = services.Catalog(self.db())
        self.assertFalse(catalog.checked(catalog.get("genshin")))                     # 맞춤 추천은 채우기로만 (C12-i)


if __name__ == "__main__":
    unittest.main()

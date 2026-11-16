"""
스토어 자동 수집 테스트 (PROJECT.md 4.11 C7-a2). 스토어에는 접속하지 않고 가짜 응답(FakeStore)을 쓴다.
실행: python -m unittest discover tests   (프로젝트 최상위에서)
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

from app.db import get_connection, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.reco import repository as repo  # noqa: E402
from app.security import limiter  # noqa: E402
from scripts import init_db  # noqa: E402
from scripts import sync_store as sync  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20
TODAY = date.today()


def play_date(d: date) -> str:
    return f"{d.year}. {d.month}. {d.day}."


def ios_app(track_id, name, developer, bundle, genres, price=0.0, ratings=300, released=None, genre_id=6014):
    return {"trackId": int(track_id), "trackName": name, "artistName": developer, "bundleId": bundle,
            "primaryGenreId": genre_id, "genreIds": [str(genre_id)],
            "genres": ["게임", *genres], "price": price, "averageUserRating": 4.5, "userRatingCount": ratings,
            "releaseDate": f"{released or TODAY - timedelta(days=5)}T07:00:00Z",
            "description": f"{name} 설명 첫 줄\n두 번째 줄", "artworkUrl512": "https://is1.example/512x512bb.jpg"}


IOS = {
    "1001": ios_app("1001", "용사 키우기", "Hero Games Co., Ltd.", "com.hero.idle", ["롤플레잉"]),
    "1002": ios_app("1002", "로얄 매치", "Other Studio", "com.other.royal", ["퍼즐"]),
    "1003": ios_app("1003", "잭팟 슬롯", "Casino Inc.", "com.casino.slot", ["카지노"]),
    "1004": ios_app("1004", "작은 정원", "ConcernedApe", "com.tiny.garden", ["시뮬레이션"], price=4.99, ratings=300),
}
PLAY = {
    "com.hero.idle": {"title": "용사 키우기 : 방치형 RPG", "developer": "HeroGames", "genreId": "GAME_ROLE_PLAYING",
                      "minInstalls": 1_000_000, "score": 4.512, "ratings": 2000, "summary": "자동 전투 방치형 판타지 RPG",
                      "released": play_date(TODAY - timedelta(days=10)), "offersIAP": True, "containsAds": True,
                      "free": True, "price": 0, "icon": "https://play-lh.example/hero", "description": ""},
    "com.supercell.brawlstars": {"title": "브롤스타즈", "developer": "Supercell", "genreId": "GAME_ACTION",
                                 "minInstalls": 500_000_000, "score": 4.2, "ratings": 9_000_000, "summary": "3:3",
                                 "released": "2018. 12. 12.", "free": True, "price": 0, "icon": None},
    "com.new.puzzle": {"title": "새 퍼즐", "developer": "Puzzle Co", "genreId": "GAME_PUZZLE", "minInstalls": 50_000,
                       "score": 4.0, "ratings": 100, "summary": "매치3 퍼즐", "released": "2025. 1. 1.",
                       "free": True, "price": 0, "icon": "https://play-lh.example/puzzle"},
}


class FakeStore:
    def __init__(self, android_fail=("grossing",)):
        self.android_fail = set(android_fail)
        self.calls = []

    def ios_chart(self, kind):
        ids = {"free": ["1001", "1002", "1003"], "grossing": ["1001"], "paid": ["1004"]}[kind]
        return [{"ios_id": i, "name": IOS[i]["trackName"]} for i in ids]

    def ios_lookup(self, ids):
        return {i: IOS[i] for i in ids if i in IOS}

    def android_chart(self, kind):
        if kind in self.android_fail:
            raise ValueError("차트 응답 형식이 바뀜")
        return {"free": ["com.hero.idle", "com.supercell.brawlstars", "com.new.puzzle"], "grossing": [], "paid": []}[kind]

    def ios_new(self, kind):
        return []

    def play_details(self, package):
        self.calls.append(package)
        return PLAY.get(package)

    def play_search(self, term, n=3):
        return []

    def image(self, url):
        return PNG


# ---------- 최근 출시작 (C7-f) ----------
NEW_IOS = {
    "2001": ios_app("2001", "새 모험", "Indie Kid", "com.new.adv", ["어드벤처"], released=TODAY - timedelta(days=3)),
    "2002": ios_app("2002", "영어 단어장", "Edu Co", "com.edu.words", ["교육"], genre_id=6017),            # 게임 아님
    "2003": ios_app("2003", "곧 나올 게임", "Soon Inc.", "com.soon.game", ["롤플레잉"],
                    released=TODAY + timedelta(days=20)),                                                   # 사전 주문
    "2004": ios_app("2004", "옛날 게임", "Old Co", "com.old.ios", ["퍼즐"], released=TODAY - timedelta(days=200)),
}
NEW_PLAY = {
    "com.fresh.rpg": {"title": "갓 나온 RPG", "developer": "Fresh Studio", "genreId": "GAME_ROLE_PLAYING",
                      "minInstalls": 1000, "score": 4.1, "ratings": 20, "summary": "턴제 판타지 RPG",
                      "released": play_date(TODAY - timedelta(days=7)), "free": True, "price": 0, "icon": None},
    "com.prereg.game": {"title": "사전 등록 게임", "developer": "Pre Co", "genreId": "GAME_ACTION", "minInstalls": 0,
                        "score": None, "ratings": 0, "summary": "사전 등록 중", "released": None, "free": True,
                        "price": 0, "icon": None},
    "com.old.game": {"title": "오래된 게임", "developer": "Old Co", "genreId": "GAME_PUZZLE", "minInstalls": 10000,
                     "score": 4.0, "ratings": 50, "summary": "퍼즐", "released": "2024. 1. 1.", "free": True,
                     "price": 0, "icon": None},
}


class NewReleaseStore(FakeStore):
    def ios_new(self, kind):
        return [{"ios_id": i, "name": IOS_NEW_NAME} for i in NEW_IOS] if kind == "new" else []

    def ios_lookup(self, ids):
        return {i: {**IOS, **NEW_IOS}[i] for i in ids if i in {**IOS, **NEW_IOS}}

    def play_details(self, package):
        self.calls.append(package)
        return {**PLAY, **NEW_PLAY}.get(package)

    def play_search(self, term, n=3):
        return ["com.fresh.rpg", "com.prereg.game", "com.old.game", "com.hero.idle"] if term == "신작 게임" else []


IOS_NEW_NAME = "신작"


class SyncTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for name, value in (("ICON_DIR", Path(self.tmp.name) / "games"), ("LOG_DIR", Path(self.tmp.name) / "logs")):
            patch = mock.patch.object(sync, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.db_path = Path(self.tmp.name) / "test.db"
        with get_connection(self.db_path) as conn:
            init_db.prepare(conn, json.loads(init_db.GAMES_JSON.read_text(encoding="utf-8")))
            conn.execute("UPDATE games SET android_id = 'com.supercell.brawlstars' WHERE id = 'brawl'")
            conn.execute("UPDATE games SET developer = 'Dream Games, Ltd.' WHERE id = 'royalmatch'")

    def run_sync(self, store=None, limit=50, new_limit=100):
        return sync.run_sync(self.db_path, store or FakeStore(), limit=limit, new_limit=new_limit)

    def db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn

    def game(self, **where):
        (key, value), = where.items()
        return self.db().execute(f"SELECT * FROM games WHERE {key} = ?", (value,)).fetchone()

    def tags(self, game_id):
        return {(f"{r['category']}:{r['name']}", r["auto"]) for r in self.db().execute(
            "SELECT t.category, t.name, gt.auto FROM game_tags gt JOIN tags t ON t.id = gt.tag_id WHERE gt.game_id = ?",
            (game_id,))}


class SyncTest(SyncTestCase):
    def test_new_game_paired_by_bundle_id(self):
        report = self.run_sync()
        hero = self.game(ios_id="1001")
        self.assertEqual(hero["android_id"], "com.hero.idle")       # bundleId = 패키지 → 한 게임으로
        self.assertEqual(hero["name"], "용사 키우기")
        self.assertEqual((hero["source"], hero["review_status"], hero["tier"]), ("auto", "ok", "중견"))
        self.assertIsNone(hero["is_indie"])
        self.assertEqual((hero["chart_rank_android"], hero["chart_rank_ios"]), (1, 1))
        self.assertEqual(hero["release_date"], (TODAY - timedelta(days=10)).isoformat())  # 두 스토어 중 이른 날
        self.assertEqual((hero["installs"], hero["store_rating"]), (1_000_000, 4.51))
        self.assertEqual(hero["thumbnail_url"], f"/static/img/games/{hero['id']}.png")
        self.assertTrue((Path(self.tmp.name) / "games" / f"{hero['id']}.png").exists())
        self.assertEqual(self.tags(hero["id"]), {(t, 1) for t in (
            "genre:RPG", "subgenre:방치형RPG", "play:자동전투", "mood:판타지", "bm:무료+과금", "bm:광고")})
        self.assertEqual(report["failed"], 0)
        self.assertTrue(any("grossing" in n for n in report["notes"]))  # Play 차트 하나 실패해도 계속

    def test_existing_game_numbers_only(self):
        self.run_sync()
        brawl = self.game(id="brawl")
        self.assertEqual((brawl["name"], brawl["source"]), ("브롤스타즈", "manual"))
        self.assertEqual((brawl["installs"], brawl["tier"], brawl["chart_rank_android"]), (500_000_000, "대형", 2))
        self.assertIsNotNone(brawl["updated_at"])
        self.assertEqual(self.tags("brawl"), {(t, 0) for t, _ in self.tags("brawl")})  # 직접 넣은 태그는 그대로

    def test_rerun_no_duplicates(self):
        self.run_sync()
        count = self.db().execute("SELECT COUNT(*) FROM games").fetchone()[0]
        report = self.run_sync()
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM games").fetchone()[0], count)
        self.assertEqual((report["added"], report["pending"]), (0, 0))
        runs = self.db().execute("SELECT status FROM sync_runs").fetchall()
        self.assertEqual([r["status"] for r in runs], ["ok", "ok"])

    def test_similar_name_other_developer_is_pending(self):
        self.run_sync()
        royal = self.game(ios_id="1002")
        self.assertEqual(royal["review_status"], "pending")
        self.assertNotIn(royal["id"], {g["id"] for g in repo.load_games(self.db())})  # 화면에 안 나옴
        self.assertEqual(self.game(id="royalmatch")["ios_id"], None)                 # 기존 게임과 합치지 않음

    def test_unmapped_genre_skipped_and_ios_only_indie(self):
        report = self.run_sync()
        self.assertIsNone(self.game(ios_id="1003"))  # 카지노
        self.assertEqual(report["skipped"], 1)
        tiny = self.game(ios_id="1004")
        self.assertIsNone(tiny["android_id"])
        self.assertEqual((tiny["is_indie"], tiny["tier"]), (1, "소형"))  # 인디 개발사 목록 / 별점 수 300
        self.assertIn(("bm:유료구매", 1), self.tags(tiny["id"]))
        self.assertEqual(tiny["description"], "작은 정원 설명 첫 줄")

    def test_android_only_game(self):
        self.run_sync()
        puzzle = self.game(android_id="com.new.puzzle")
        self.assertEqual((puzzle["ios_id"], puzzle["tier"], puzzle["chart_rank_android"]), (None, "소형", 3))
        self.assertIn(("subgenre:매치3", 1), self.tags(puzzle["id"]))

    def test_tier_locked_not_overwritten(self):
        self.db().execute("UPDATE games SET tier = '중견', tier_locked = 1 WHERE id = 'brawl'").connection.commit()
        self.run_sync()
        self.assertEqual(self.game(id="brawl")["tier"], "중견")

    def test_new_game_limit(self):
        report = self.run_sync(limit=1)
        self.assertEqual(report["added"] + report["pending"], 1)
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM games WHERE source = 'auto'").fetchone()[0], 1)

    def test_chart_failure_keeps_old_ranks(self):
        self.run_sync()
        self.run_sync(FakeStore(android_fail=("free", "grossing", "paid")))
        self.assertEqual(self.game(id="brawl")["chart_rank_android"], 2)

    def test_rejected_game_not_added_again(self):
        self.run_sync()
        pending_id = self.game(ios_id="1002")["id"]
        with mock.patch("app.db.DB_PATH", self.db_path), mock.patch("builtins.print"):
            sync.set_review([pending_id], "rejected")
            sync.set_review(["brawl"], "rejected")  # 직접 넣은 게임은 안 바뀜
        self.run_sync()
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM games WHERE ios_id = '1002'").fetchone()[0], 1)
        self.assertEqual(self.game(id="brawl")["review_status"], "ok")

    def test_skip_when_another_run_is_running(self):
        self.db().execute("INSERT INTO sync_runs (started_at, status) VALUES (datetime('now'), 'running')").connection.commit()
        self.assertEqual(self.run_sync(), {"skipped_run": True})


class NewReleaseTest(SyncTestCase):
    """최근 출시작 전부 수집 (C7-f)"""

    def test_new_releases_added_and_filtered(self):
        report = self.run_sync(NewReleaseStore())
        adv = self.game(ios_id="2001")
        self.assertEqual((adv["source_feed"], adv["review_status"], adv["android_id"]), ("new", "ok", None))
        fresh = self.game(android_id="com.fresh.rpg")
        self.assertEqual((fresh["source_feed"], fresh["installs"], fresh["ios_id"]), ("new", 1000, None))
        self.assertIn(("subgenre:턴제RPG", 1), self.tags(fresh["id"]))
        for where in ({"ios_id": "2002"}, {"ios_id": "2003"}, {"ios_id": "2004"},
                      {"android_id": "com.prereg.game"}, {"android_id": "com.old.game"}):
            self.assertIsNone(self.game(**where), where)  # 게임 아님 / 사전 주문 / 90일 넘음 / 사전 등록 / 90일 넘음
        self.assertEqual((report["new_not_game"], report["new_prerelease"], report["new_too_old"]), (1, 2, 2))
        self.assertEqual((report["new_added"], report["new_ios_only"], report["new_android_only"]), (2, 1, 1))
        self.assertEqual((report["pending"], report["new_pending"]), (1, 0))  # 차트 검수 대기(로얄 매치)는 1번만 셈
        self.assertEqual(self.game(ios_id="1001")["source_feed"], "chart")   # 차트 경로
        self.assertEqual(self.game(id="brawl")["source_feed"], "manual")

    def test_new_release_limit_is_separate(self):
        report = self.run_sync(NewReleaseStore(), limit=1, new_limit=1)
        self.assertEqual(report["added"] + report["pending"], 1)   # 차트 상한
        self.assertEqual(report["new_added"], 1)                    # 신작 상한은 따로
        self.assertGreaterEqual(report["new_capped"], 1)

    def test_rerun_no_duplicates(self):
        self.run_sync(NewReleaseStore())
        count = self.db().execute("SELECT COUNT(*) FROM games").fetchone()[0]
        report = self.run_sync(NewReleaseStore())
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM games").fetchone()[0], count)
        self.assertEqual((report["new_added"], report["new_pending"]), (0, 0))

    def test_prune_new_hides_without_deleting(self):
        self.run_sync(NewReleaseStore())
        conn = self.db()
        fresh = self.game(android_id="com.fresh.rpg")["id"]
        adv = self.game(ios_id="2001")["id"]
        self.assertEqual(sync.prune_new(conn, 5000, None, dry_run=True), [fresh])
        self.assertEqual(self.game(id=fresh)["review_status"], "ok")             # 미리 보기는 안 바꿈
        self.assertEqual(sync.prune_new(conn, 5000, None), [fresh])              # 설치 수 모르는 iOS만 게임은 그대로
        self.assertEqual(sync.prune_new(conn, None, 1000), [adv])                 # 별점 수 300 < 1000
        conn.commit()
        self.assertEqual({self.game(id=i)["review_status"] for i in (fresh, adv)}, {"rejected"})
        self.assertEqual(self.game(ios_id="1001")["review_status"], "ok")        # 차트 경로는 안 건드림
        self.assertNotIn(fresh, {g["id"] for g in repo.load_games(conn)})
        with self.assertRaises(ValueError):
            sync.prune_new(conn, None, None)

    def test_existing_prerelease_reported(self):
        self.db().execute("UPDATE games SET release_date = ? WHERE id = 'gris'",
                          ((TODAY + timedelta(days=5)).isoformat(),)).connection.commit()
        report = self.run_sync()
        self.assertTrue(any(line.startswith("gris ") for line in report["existing_prerelease"]))
        self.assertIsNotNone(self.game(id="gris"))  # 지우지 않음


class PairByNameStore(FakeStore):
    """iOS 게임의 Play 짝을 이름 검색으로만 찾고, 찾은 앱은 이름만 비슷한 다른 개발사 앱 (C8-a)"""
    APP = ios_app("3001", "Pixel Harmony", "Beril Bolukbasi", "com.pix.harmony", ["퍼즐"])
    WRONG = {"title": "Harmony®", "developer": "Logitech Europe S.A.", "genreId": "GAME_PUZZLE",
             "minInstalls": 1_000_000, "score": 4.0, "ratings": 100, "summary": "리모컨", "released": "2020. 1. 1.",
             "free": True, "price": 0, "icon": None}

    def ios_chart(self, kind):
        return [{"ios_id": "3001", "name": "Pixel Harmony"}] if kind == "free" else []

    def ios_lookup(self, ids):
        return {i: self.APP for i in ids if i == "3001"}

    def android_chart(self, kind):
        return []

    def play_details(self, package):
        return self.WRONG if package == "com.logitech.harmonyhub" else None

    def play_search(self, term, n=3):
        return ["com.logitech.harmonyhub"]


class PendingRuleTest(SyncTestCase):
    """C8-a: 검수 대기는 DB에 이미 있는 게임과 이름이 비슷할 때만"""

    def test_name_search_pair_with_other_developer_goes_ios_only(self):
        report = self.run_sync(PairByNameStore())
        game = self.game(ios_id="3001")
        self.assertEqual((game["review_status"], game["android_id"]), ("ok", None))
        self.assertEqual((report["added"], report["pending"]), (1, 0))

    def test_recheck_pending(self):
        conn = self.db()
        conn.execute("INSERT INTO games (id, name, developer, source, review_status) VALUES "
                     "('p-unique', 'Pixel Harmony', 'Beril', 'auto', 'pending'), "
                     "('p-similar', '로얄 매치', 'Other Studio', 'auto', 'pending')")
        conn.commit()
        preview = sync.recheck_pending(conn, dry_run=True)
        self.assertEqual({p["id"]: p["status"] for p in preview}, {"p-unique": "ok", "p-similar": "pending"})
        self.assertEqual(self.game(id="p-unique")["review_status"], "pending")  # 미리 보기는 안 바꿈
        result = sync.recheck_pending(conn)
        conn.commit()
        self.assertTrue(any("royalmatch" in s for s in next(r for r in result if r["id"] == "p-similar")["similar"]))
        self.assertEqual(self.game(id="p-unique")["review_status"], "ok")
        self.assertEqual(self.game(id="p-similar")["review_status"], "pending")


class MatchRuleTest(unittest.TestCase):
    def test_developer_normalized(self):
        self.assertTrue(sync.same_developer("Ryong Game Co., Ltd", "RyongGames"))
        self.assertTrue(sync.same_developer("NEXON Company", "Nexon"))
        self.assertFalse(sync.same_developer("Supercell", "King"))
        self.assertFalse(sync.same_developer(None, "King"))

    def test_name_variants(self):
        self.assertGreaterEqual(sync.name_similarity("드래곤 나이트 키우기", "드래곤 나이트 키우기 : 방치형 RPG"), sync.NAME_MATCH)
        self.assertGreaterEqual(sync.name_similarity("Among Us (어몽어스)", "어몽어스"), sync.NAME_MATCH)
        self.assertLess(sync.name_similarity("쿠키런: 킹덤", "쿠키런: 오븐브레이크"), sync.NAME_MATCH)

    def test_tier_rules(self):
        self.assertEqual([sync.classify_tier(n, None) for n in (10_000_000, 1_000_000, 999_999)], ["대형", "중견", "소형"])
        self.assertEqual([sync.classify_tier(None, n) for n in (50_000, 5_000, 10)], ["대형", "중견", "소형"])
        self.assertIsNone(sync.classify_tier(None, None))

    def test_english_keyword_whole_word(self):
        self.assertEqual(sync.keyword_tags("SF 우주 전쟁", {"mood:SF": ["SF"]}), ["mood:SF"])
        self.assertEqual(sync.keyword_tags("SFX 효과음", {"mood:SF": ["SF"]}), [])

    def test_parse_date(self):
        self.assertEqual(sync.parse_date("2026. 10. 27."), "2026-10-27")
        self.assertEqual(sync.parse_date("2026-10-28T07:00:00Z"), "2026-10-28")
        self.assertIsNone(sync.parse_date(None))


class MigrationTest(unittest.TestCase):
    def test_old_tier_check_migrated(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        conn.execute("PRAGMA foreign_keys = ON")
        old = init_db.SCHEMA.replace("'소형'", "'인디'")  # 예전 CHECK (대형/중견/인디)
        conn.executescript(old)
        conn.execute("INSERT INTO games (id, name, tier) VALUES ('gris', 'GRIS', '인디'), ('dq8', 'DQ8', NULL)")
        conn.execute("INSERT INTO tags (name, category) VALUES ('어드벤처', 'genre')")
        conn.execute("INSERT INTO game_tags (game_id, tag_id) VALUES ('gris', 1)")
        conn.execute("INSERT INTO users (id) VALUES ('u1')")
        conn.execute("INSERT INTO reactions (user_id, game_id, type) VALUES ('u1', 'gris', 'like')")
        conn.commit()
        self.assertTrue(init_db.migrate_games_tier(conn))
        self.assertFalse(init_db.migrate_games_tier(conn))  # 두 번째는 할 일 없음
        gris = conn.execute("SELECT tier, is_indie FROM games WHERE id = 'gris'").fetchone()
        self.assertEqual((gris["tier"], gris["is_indie"]), (None, 1))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM game_tags").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM reactions").fetchone()[0], 1)
        conn.execute("UPDATE games SET tier = '소형' WHERE id = 'dq8'")
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        conn.execute("DELETE FROM games WHERE id = 'gris'")  # 외래 키 연결이 살아 있음 (연쇄 삭제)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM reactions").fetchone()[0], 0)


class HomeTabsTest(SyncTestCase):
    def setUp(self):
        super().setUp()

        def _test_db():
            with get_connection(self.db_path) as conn:
                yield conn

        app.dependency_overrides[get_db] = _test_db
        self.addCleanup(app.dependency_overrides.clear)
        limiter.reset()
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.client.post("/api/users")

    def test_popular_is_store_chart_rank(self):
        self.run_sync()
        games = self.client.get("/api/feed?section=popular").json()["games"]
        self.assertEqual([g["chart_rank"] for g in games], sorted(g["chart_rank"] for g in games))
        self.assertEqual(games[0]["name"], "용사 키우기")
        self.assertNotIn("로얄 매치", [g["name"] for g in games if g["id"] != "royalmatch"])  # 검수 대기는 안 나옴

    def test_new_is_last_90_days(self):
        conn = self.db()
        conn.execute("UPDATE games SET release_date = ? WHERE id = 'gris'", ((TODAY - timedelta(days=30)).isoformat(),))
        conn.execute("UPDATE games SET release_date = ? WHERE id = 'dq8'", ((TODAY - timedelta(days=91)).isoformat(),))
        conn.execute("UPDATE games SET release_date = ? WHERE id = 'ff6'", ((TODAY + timedelta(days=10)).isoformat(),))
        conn.commit()
        games = self.client.get("/api/feed?section=new").json()["games"]
        self.assertEqual([g["id"] for g in games], ["gris"])


if __name__ == "__main__":
    unittest.main()

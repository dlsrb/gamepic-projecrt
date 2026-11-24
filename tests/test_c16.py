"""
C16 규모 확장 실험 테스트 (PROJECT.md 4.11 C16). 스토어에는 접속하지 않음 (가짜 응답).
- sync_store.py --db/--target/--genre-charts: 실험 DB에만 쓰고, 아이콘 안 받고, 장르 몫 상한을 지킴
- 실제 서버의 매일 수집(run_sync 기본값)은 장르 차트를 안 씀
- simulate_users.py --db: 실험 DB에서 바로 시뮬, 실제 DB·seed는 그대로
- big.db·실험 결과 파일은 git·배포 zip에 안 들어감
실행: python -m unittest discover tests   (프로젝트 최상위에서)
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from tests.test_sync_store import IOS, PLAY, PNG, FakeStore, SyncTestCase, ios_app  # noqa: E402
from scripts import pack_for_replit, simulate_users  # noqa: E402
from scripts import sync_store as sync  # noqa: E402

REAL_DB = BASE / "data" / "gamepick.db"
SEED_DB = BASE / "data" / "seed.db"


def digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


# 장르 차트용 가짜 게임: RPG 차트에 많이, 퍼즐 차트에 조금
GENRE_IOS = {str(3000 + i): ios_app(str(3000 + i), f"장르 RPG {i}", f"Dev R{i}", f"com.genre.rpg{i}", ["롤플레잉"],
                                     genre_id=7014) for i in range(12)}
GENRE_IOS.update({str(3100 + i): ios_app(str(3100 + i), f"장르 퍼즐 {i}", f"Dev P{i}", f"com.genre.puz{i}", ["퍼즐"],
                                          genre_id=7012) for i in range(3)})
GENRE_PLAY = {f"com.play.rpg{i}": {"title": f"플레이 RPG {i}", "developer": f"PDev {i}", "genreId": "GAME_ROLE_PLAYING",
                                   "minInstalls": 100_000, "score": 4.3, "ratings": 500, "summary": "판타지 RPG",
                                   "released": "2024. 5. 1.", "free": True, "price": 0,
                                   "icon": "https://play-lh.example/x"} for i in range(6)}


class GenreStore(FakeStore):
    def __init__(self):
        super().__init__(android_fail=())
        self.genre_calls = []

    def ios_genre_chart(self, kind, genre):
        self.genre_calls.append(("ios", kind, genre))
        if kind != "free":
            return []
        prefix = {7014: "30", 7012: "31"}.get(genre)
        return [{"ios_id": i, "name": GENRE_IOS[i]["trackName"]} for i in GENRE_IOS if prefix and i.startswith(prefix)]

    def android_chart(self, kind, category="GAME", size=sync.CHART_SIZE):
        if category != "GAME":
            self.genre_calls.append(("android", kind, category))
            return list(GENRE_PLAY) if (kind, category) == ("free", "GAME_ROLE_PLAYING") else []
        return super().android_chart(kind)

    def ios_lookup(self, ids):
        both = {**IOS, **GENRE_IOS}
        return {i: both[i] for i in ids if i in both}

    def play_details(self, package):
        self.calls.append(package)
        return {**PLAY, **GENRE_PLAY}.get(package)

    def image(self, url):
        self.calls.append(("image", url))
        return PNG


class BigCollectTest(SyncTestCase):
    def setUp(self):
        super().setUp()
        self.big = Path(self.tmp.name) / "big.db"
        patch = mock.patch.object(sync, "DB_PATH", self.db_path)   # 이 테스트의 "실제 DB" = 임시 DB
        patch.start()
        self.addCleanup(patch.stop)

    def visible(self, path):
        conn = sqlite3.connect(path)
        try:
            return conn.execute("SELECT COUNT(*) FROM games WHERE review_status = 'ok'").fetchone()[0]
        finally:
            conn.close()

    def test_collects_into_copy_only(self):
        source_hash, start = digest(self.db_path), self.visible(self.db_path)
        store = GenreStore()
        report = sync.big_collect(self.big, start + 10, client=store)
        self.assertEqual(digest(self.db_path), source_hash)             # 원본은 그대로
        big = report["big"]
        self.assertEqual(big["start_visible"], start)
        self.assertGreater(big["end_visible"], start)
        self.assertLessEqual(big["end_visible"], start + 10)             # 목표를 넘겨 채우지 않음
        self.assertEqual(self.visible(self.big), big["end_visible"])
        self.assertFalse([c for c in store.calls if isinstance(c, tuple) and c[0] == "image"])  # 아이콘 안 받음
        self.assertFalse(any((Path(self.tmp.name) / "games").glob("*")))

    def test_genre_share_cap(self):
        start = self.visible(self.db_path)
        report = sync.big_collect(self.big, start + 10, client=GenreStore())
        big = report["big"]
        cap = max(1, int(big["chart_quota"] * sync.GENRE_SHARE_MAX))
        for genre, n in big["chart_added_by_queue"].items():
            self.assertLessEqual(n, cap, genre)                          # 한 장르가 차트 몫의 절반을 넘지 않음
        self.assertIn("퍼즐", big["chart_added_by_queue"])               # 다른 장르도 돌아가며 들어옴

    def test_new_games_get_tags(self):
        start = self.visible(self.db_path)
        sync.big_collect(self.big, start + 6, client=GenreStore())
        conn = sqlite3.connect(self.big)
        try:
            untagged = conn.execute(
                "SELECT COUNT(*) FROM games g WHERE (g.ios_id LIKE '30%' OR g.android_id LIKE 'com.play.rpg%') "
                "AND NOT EXISTS (SELECT 1 FROM game_tags t WHERE t.game_id = g.id)").fetchone()[0]
            added = conn.execute("SELECT COUNT(*) FROM games WHERE ios_id LIKE '30%' "
                                 "OR android_id LIKE 'com.play.rpg%'").fetchone()[0]
        finally:
            conn.close()
        self.assertGreater(added, 0)
        self.assertEqual(untagged, 0)                                    # 자동 태그는 평소 규칙대로

    def test_refuses_real_db_and_seed(self):
        for target in (self.db_path, sync.DATA_DIR / "seed.db"):
            with self.assertRaises(SystemExit):
                sync.big_collect(target, 100, client=GenreStore())

    def test_options_must_go_together(self):
        for argv in (["--db", str(self.big)], ["--target", "100"], ["--genre-charts"],
                     ["--db", str(self.big), "--target", "100"]):
            with self.assertRaises(SystemExit):
                sync.main(argv)
        self.assertFalse(self.big.exists())

    def test_daily_sync_scope_unchanged(self):
        store = GenreStore()
        report = self.run_sync(store)                                    # 실제 서버가 부르는 run_sync 기본값
        self.assertEqual(store.genre_calls, [])                          # 장르 차트 안 씀
        self.assertNotIn("big", report)
        self.assertTrue([c for c in store.calls if isinstance(c, tuple) and c[0] == "image"])  # 아이콘은 평소처럼


class StoreSyncDefaultsTest(unittest.TestCase):
    def test_server_calls_run_sync_without_target(self):
        source = (BASE / "app" / "store_sync.py").read_text(encoding="utf-8")
        self.assertIn("report = run_sync()", source)                     # 서버는 옵션 없이 부름 → 평소 범위
        self.assertNotIn("target", source)
        self.assertNotIn("genre", source.lower())


@unittest.skipUnless(REAL_DB.exists(), "data/gamepick.db가 없어 시뮬 --db 확인을 건너뜀")
class SimulateInPlaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.before = {p: digest(p) for p in (REAL_DB, SEED_DB)}
        cls.db = Path(cls.tmp.name) / "big.db"
        src = sqlite3.connect(f"{REAL_DB.as_uri()}?mode=ro", uri=True)
        dst = sqlite3.connect(cls.db)
        src.backup(dst)
        src.close()
        dst.close()
        cls.first = cls.run_sim(force=False)
        cls.refused = cls.run_sim(force=False, check=False)
        cls.second = cls.run_sim(force=True)
        cls.after = {p: digest(p) for p in (REAL_DB, SEED_DB)}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def run_sim(cls, force: bool, check: bool = True):
        env = {k: v for k, v in os.environ.items() if k not in ("DB_PATH", "RATE_LIMITS", "PYTHONHASHSEED")}
        cmd = [sys.executable, str(BASE / "scripts" / "simulate_users.py"), "--db", str(cls.db), "--users", "5",
               "--days", "2", "--seed", "3", "--no-bench"] + (["--force"] if force else [])
        done = subprocess.run(cmd, capture_output=True, env=env, timeout=600)
        if not check:
            return done.returncode
        if done.returncode:
            raise AssertionError(done.stderr.decode("utf-8", "replace"))
        metrics = Path(cls.tmp.name) / "big_metrics.json"
        return {"metrics": json.loads(metrics.read_text(encoding="utf-8")), "stderr": done.stderr.decode("utf-8")}

    def test_real_db_and_seed_untouched(self):
        self.assertEqual(self.before, self.after)

    def test_outputs_next_to_experiment_db(self):
        self.assertTrue((Path(self.tmp.name) / "big_base.db").exists())  # 시뮬 전 상태 백업
        self.assertTrue((Path(self.tmp.name) / "big_sim_report.md").exists())
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sim_users WHERE kind = 'user'").fetchone()[0], 5)
        finally:
            conn.close()

    def test_rerun_needs_force_and_starts_from_backup(self):
        self.assertNotEqual(self.refused, 0)
        volatile = {"elapsed_seconds", "speed", "first_day", "last_day"}
        a, b = ({k: v for k, v in r["metrics"].items() if k not in volatile} for r in (self.first, self.second))
        self.assertEqual(a, b)                                           # 백업에서 다시 시작 → 같은 결과

    def test_progress_and_search_fields(self):
        self.assertIn("[시뮬] 2/2일", self.first["stderr"])
        search = self.first["metrics"]["search"]
        self.assertIn("partial", search)
        self.assertIn("zero_count", search)

    def test_refuses_real_db(self):
        for target in (REAL_DB, SEED_DB):
            with self.assertRaises(SystemExit):
                simulate_users.prepare_in_place(target, force=True)


class BigFilesIgnoredTest(unittest.TestCase):
    FILES = ("data/big.db", "data/big_base.db", "data/big_metrics.json", "data/big_collect.json",
             "data/big_sim_report.md", "data/big_c14base_metrics.json")

    def test_not_in_zip(self):
        patterns = pack_for_replit.read_ignore(BASE) + pack_for_replit.ALWAYS_SKIP
        for rel in self.FILES:
            self.assertTrue(pack_for_replit.ignored(rel, patterns), rel)

    @unittest.skipUnless((BASE / ".git").exists(), "git 저장소가 아님")
    def test_not_in_git(self):
        for rel in self.FILES:
            ok = subprocess.run(["git", "check-ignore", "-q", rel], cwd=BASE).returncode == 0
            self.assertTrue(ok, f"{rel}가 .gitignore에 안 걸림")
        tracked = subprocess.run(["git", "ls-files", "data"], cwd=BASE, capture_output=True, text=True).stdout
        self.assertNotIn("big", tracked)


if __name__ == "__main__":
    unittest.main()

"""
C10 테스트 (docs/C10_배포_지시.md): 백업·복원, 시작용 DB, 아이콘, IP·사용자 생성 제한, 출처 검사, 시작·헬스.
Object Storage는 가짜(FakeStorage), 스토어는 가짜(FakeStoreClient)로 바꾼다. 네트워크 안 씀.
실행: python -m unittest discover tests   (프로젝트 최상위에서)
"""
import shutil
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import backup, config, icons  # noqa: E402
from app.main import app  # noqa: E402
from scripts import backup_db, make_seed_db, pack_for_replit  # noqa: E402
from tests.test_api import ApiTestCase  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\nfake"


class FakeStorage:
    """Object Storage 대신: 이름 → 바이트"""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.uploads: list[str] = []
        self.fail = False

    def upload(self, name, path):
        if self.fail:
            raise ConnectionError("저장소 오류")
        self.objects[name] = Path(path).read_bytes()
        self.uploads.append(name)

    def download(self, name, path):
        if self.fail:
            raise ConnectionError("저장소 오류")
        if name not in self.objects:
            return False
        Path(path).write_bytes(self.objects[name])
        return True

    def names(self, prefix=""):
        return [n for n in self.objects if n.startswith(prefix)]

    def exists(self, name):
        if self.fail:
            raise ConnectionError("저장소 오류")
        return name in self.objects

    def copy(self, src, dest):
        self.objects[dest] = self.objects[src]

    def delete(self, name):
        self.objects.pop(name, None)

    def put_text(self, name, text):
        self.objects[name] = text.encode()


def scalar(path, sql: str):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def games_in(path) -> int:
    return scalar(path, "SELECT COUNT(*) FROM games")


class BackupTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        backup.status.reset()
        self.addCleanup(backup.status.reset)
        self.storage = FakeStorage()
        self.day = date(2026, 11, 20)

    def test_snapshot_is_consistent_copy(self):
        out = Path(self.tmp.name) / "copy.db"
        digest = backup.snapshot(self.db_path, out)
        self.assertEqual(games_in(out), games_in(self.db_path))
        self.assertEqual(digest, backup.snapshot(self.db_path, Path(self.tmp.name) / "copy2.db"))
        backup.check_db(out)  # 무결성 ok

    def test_unchanged_not_uploaded_daily_once(self):
        first = backup.backup_once(self.storage, self.db_path, self.day)
        self.assertEqual((first["latest"], first["daily"]), (True, True))
        self.assertEqual(sorted(self.storage.objects), ["gamepick-20261120.db", "gamepick-latest.db"])
        second = backup.backup_once(self.storage, self.db_path, self.day)
        self.assertEqual((second["latest"], second["daily"]), (False, False))        # 바뀐 게 없음
        self.client.post("/api/users")                                               # DB가 바뀜
        third = backup.backup_once(self.storage, self.db_path, self.day)
        self.assertEqual((third["latest"], third["daily"]), (True, False))
        fourth = backup.backup_once(self.storage, self.db_path, date(2026, 11, 21))  # 다음 날
        self.assertEqual((fourth["latest"], fourth["daily"]), (False, True))
        self.assertEqual(self.storage.uploads.count("gamepick-latest.db"), 2)
        self.assertIsNotNone(backup.status.public()["last_success"])

    def test_prune_after_14_days(self):
        for name in ("gamepick-20261105.db", "gamepick-20261106.db", "gamepick-20261119.db",
                     "gamepick-before-restore-20261101-000000.db", "gamepick-latest.db", "icons/a.png"):
            self.storage.objects[name] = b"x"
        removed = backup.prune_daily(self.storage, self.day)
        self.assertEqual(removed, ["gamepick-20261105.db"])                          # 11/6 = 딱 14일 전 → 남김
        self.assertIn("gamepick-before-restore-20261101-000000.db", self.storage.objects)

    def test_failure_counts_and_server_continues(self):
        self.storage.fail = True
        with self.assertRaises(ConnectionError):
            backup.backup_once(self.storage, self.db_path, self.day)
        runner = backup.Runner(self.storage)
        with mock.patch.object(config, "DB_PATH", self.db_path), self.assertLogs("uvicorn.error", "ERROR"):
            runner.tick()                                                            # 예외를 밖으로 안 던짐
        self.assertEqual(backup.status.public()["consecutive_failures"], 2)
        self.storage.fail = False
        with mock.patch.object(config, "DB_PATH", self.db_path):
            runner.tick()
        self.assertEqual(backup.status.public()["consecutive_failures"], 0)

    def test_restore_when_missing(self):
        target = Path(self.tmp.name) / "restored" / "gamepick.db"
        seed = Path(self.tmp.name) / "seed.db"
        shutil.copyfile(self.db_path, seed)
        # 1) 백업도 seed도 없음
        self.assertEqual(backup.restore_if_missing(self.storage, target, Path(self.tmp.name) / "none.db"), "none")
        # 2) 백업 없음 → seed
        self.assertEqual(backup.restore_if_missing(self.storage, target, seed), "seed")
        self.assertEqual(backup.restore_if_missing(self.storage, target, seed), "kept")   # 이미 있으면 그대로
        # 3) latest 있으면 latest
        target.unlink()
        self.client.post("/api/users")
        backup.backup_once(self.storage, self.db_path, self.day)
        self.assertEqual(backup.restore_if_missing(self.storage, target, seed), "latest")
        self.assertEqual(scalar(target, "SELECT COUNT(*) FROM users"), 1)

    def test_restore_refuses_seed_when_only_daily_or_storage_error(self):
        target = Path(self.tmp.name) / "x.db"
        self.storage.objects["gamepick-20261119.db"] = b"x"
        with self.assertRaises(RuntimeError):                     # latest만 없고 일별은 있음 → 빈 DB로 시작 안 함
            backup.restore_if_missing(self.storage, target, self.db_path)
        self.storage.fail = True
        with self.assertRaises(ConnectionError):                  # 저장소 오류 → seed로 넘어가지 않고 시작 실패
            backup.restore_if_missing(self.storage, target, self.db_path)
        self.assertFalse(target.exists())

    def test_restore_command_and_marker(self):
        backup.backup_once(self.storage, self.db_path, date(2026, 11, 19))
        old_latest = self.storage.objects["gamepick-latest.db"]
        self.storage.objects["gamepick-latest.db"] = b"newer"
        saved = backup_db.restore(self.storage, "20261119", now=datetime(2026, 11, 21, 3, 4, 5, tzinfo=timezone.utc))
        self.assertEqual(saved, "gamepick-before-restore-20261121-030405.db")
        self.assertEqual(self.storage.objects[saved], b"newer")
        self.assertEqual(self.storage.objects["gamepick-latest.db"], old_latest)
        self.assertIn(backup.RESTORE_MARKER, self.storage.objects)
        with self.assertRaises(ValueError):
            backup_db.restore(self.storage, "2026-11-19")
        with self.assertRaises(FileNotFoundError):
            backup_db.restore(self.storage, "20200101")
        # 이전 서버(표시 전부터 켜져 있던)는 DB가 바뀌어도 latest를 덮어쓰지 않음
        self.client.post("/api/users")
        result = backup.backup_once(self.storage, self.db_path, date(2026, 11, 21), final=True)
        self.assertTrue(result["skipped_for_restore"])
        self.assertEqual(self.storage.objects["gamepick-latest.db"], old_latest)
        # 새 서버: 표시가 있는 latest로 복원 → 첫 백업 때 올리고 표시를 지움
        target = Path(self.tmp.name) / "new.db"
        self.assertEqual(backup.restore_if_missing(self.storage, target, None), "latest-marked")
        self.assertIn(backup.RESTORE_MARKER, self.storage.objects)                 # 복원 때는 안 지움
        backup.status.reset()
        result = backup.backup_once(self.storage, target, date(2026, 11, 21), restored_from_marker=True)
        self.assertTrue(result["latest"])
        self.assertNotIn(backup.RESTORE_MARKER, self.storage.objects)

    def test_ensure_schema_adds_new_tables_to_old_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("DROP TABLE comment_votes")
        conn.commit()
        conn.close()
        backup.ensure_schema(self.db_path)
        self.assertEqual(scalar(self.db_path, "SELECT COUNT(*) FROM sqlite_master WHERE name = 'comment_votes'"), 1)


class SeedAndPackTest(ApiTestCase):
    def test_seed_has_no_users_and_same_games(self):
        self.client.post("/api/users")
        self.client.post("/api/games/dq8/comments", json={"body": "지워질 댓글"})
        self.client.post("/api/reactions", json={"game_id": "dq8", "type": "like"})
        self.client.get("/api/search?q=RPG")
        conn = self.db()
        conn.execute("UPDATE games SET review_status = 'pending' WHERE id = 'gris'")
        conn.commit()
        out = Path(self.tmp.name) / "seed.db"
        counts = make_seed_db.make_seed(self.db_path, out)
        self.assertEqual(counts["games"], games_in(self.db_path))
        for table in make_seed_db.USER_TABLES:
            self.assertEqual(counts[table], 0, table)
        self.assertEqual(scalar(out, "SELECT review_status FROM games WHERE id = 'gris'"), "pending")

    def test_pack_excludes_ignored_and_secrets_includes_seed(self):
        base = Path(self.tmp.name) / "proj"
        files = {".gitignore": "data/*.db\nstatic/img/games/\nlogs/\n__pycache__/\n.env\n",
                 "app/main.py": "x", "app/__pycache__/m.pyc": "x", ".env": "SECRET=1", "data/gamepick.db": "x",
                 "data/seed.db": "x", "data/survey.json": "{}", "static/img/games/a.png": "x", "static/css/b.css": "x",
                 "logs/a.log": "x", "legacy/old.py": "x", ".claude/launch.json": "x", ".replit": "x",
                 "sub/.env": "SECRET=2"}
        for rel, text in files.items():
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            (base / rel).write_text(text, encoding="utf-8")
        out = Path(self.tmp.name) / "out.zip"
        pack_for_replit.pack(base, out)
        names = sorted(zipfile.ZipFile(out).namelist())
        self.assertEqual(names, [".gitignore", ".replit", "app/main.py", "data/seed.db", "data/survey.json",
                                 "static/css/b.css"])


class FakeStoreClient:
    def __init__(self):
        self.calls = []

    def play_details(self, package):
        self.calls.append(package)
        return {"icon": f"https://play.example/{package}"} if package != "broken" else None

    def ios_lookup(self, ids):
        return {i: {"trackId": int(i), "artworkUrl512": "https://ios.example/512x512bb.png"} for i in ids}

    def image(self, url):
        return b"<html>" if "bad" in url else PNG


class IconTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.static = Path(self.tmp.name) / "static"
        for name, value in (("STATIC_DIR", self.static), ("DB_PATH", self.db_path)):
            patch = mock.patch.object(config, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        conn = self.db()
        conn.execute("UPDATE games SET thumbnail_url = '/static/img/games/' || id || '.png', "
                     "android_id = NULL, ios_id = NULL")
        conn.execute("UPDATE games SET android_id = 'com.a' WHERE id = 'dq8'")
        conn.execute("UPDATE games SET android_id = 'broken', ios_id = '123' WHERE id = 'gris'")   # Play 없음 → iOS
        conn.execute("UPDATE games SET android_id = 'com.bad' WHERE id = 'chrono'")
        conn.execute("UPDATE games SET thumbnail_url = NULL WHERE id NOT IN ('dq8', 'gris', 'chrono', 'ff6')")
        conn.commit()

    def test_refill_missing_only_and_continue_on_failure(self):
        (self.static / "img" / "games").mkdir(parents=True)
        client = FakeStoreClient()
        client.image = lambda url: b"<html>" if "com.bad" in url else PNG
        with self.assertLogs("uvicorn.error", "WARNING"):
            result = icons.refill(client)
        self.assertEqual((result["missing"], result["saved"], sorted(result["failed"])), (4, 2, ["chrono", "ff6"]))
        self.assertEqual((self.static / "img/games/gris.png").read_bytes(), PNG)
        self.assertEqual(icons.refill(client)["missing"], 2)                       # 받은 것은 다시 안 받음

    def test_icons_storage_restore_and_upload(self):
        storage = FakeStorage()
        storage.objects.update({"icons/dq8.png": PNG, "icons/../evil.png": PNG, "icons/x.txt": b"x"})
        self.assertEqual(backup.restore_icons(storage), 1)
        self.assertTrue((self.static / "img/games/dq8.png").exists())
        self.assertFalse((self.static / "img/evil.png").exists())
        (self.static / "img/games/gris.png").write_bytes(PNG)
        uploaded = set(storage.names("icons/"))
        self.assertEqual(backup.upload_icons(storage, uploaded), 1)
        self.assertEqual(backup.upload_icons(storage, uploaded), 0)
        self.assertEqual(storage.objects["icons/gris.png"], PNG)


class ClientIpAndLimitsTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        for name, value in (("PROXY_HOPS", 1), ("USER_CREATE_PER_HOUR", 3)):
            patch = mock.patch.object(config, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def new(self, xff=None):
        client = TestClient(app)
        self.addCleanup(client.close)
        return client.post("/api/users", headers={"X-Forwarded-For": xff} if xff else {})

    def test_user_create_per_ip_per_hour(self):
        codes = [self.new("1.1.1.1").status_code for _ in range(4)]
        self.assertEqual(codes, [200, 200, 200, 429])
        self.assertEqual(self.new("2.2.2.2").status_code, 200)                       # 다른 IP는 따로
        # 왼쪽에 꾸민 값을 넣어도 오른쪽(프록시가 붙인) 값으로 셈
        self.assertEqual(self.new("9.9.9.9, 1.1.1.1").status_code, 429)
        # 쿠키가 있는 기존 사용자는 상관없음
        self.assertEqual(self.client.get("/api/me").status_code, 200)

    def test_hops_zero_uses_socket_address(self):
        with mock.patch.object(config, "PROXY_HOPS", 0):
            codes = [self.new(f"{i}.{i}.{i}.{i}").status_code for i in range(1, 5)]   # 헤더 무시 → 모두 같은 주소
        self.assertEqual(codes, [200, 200, 200, 429])

    def test_other_ip_limits_use_forwarded_ip(self):
        with mock.patch.object(config, "USER_CREATE_PER_HOUR", 100):
            codes = []
            for ip in ("3.3.3.3", "4.4.4.4"):
                client = TestClient(app, headers={"X-Forwarded-For": ip})
                self.addCleanup(client.close)
                client.post("/api/users")
                codes += [client.post("/api/games/dq8/comments", json={"body": f"{ip} {i}"}).status_code
                          for i in range(5)]
        self.assertEqual(codes, [201] * 10)                                          # IP당 분당 5 → 따로 셈


class OriginAndStartupTest(ApiTestCase):
    def test_origin_check(self):
        self.client.post("/api/users")
        evil = self.client.post("/api/reactions", json={"game_id": "dq8", "type": "like"},
                                headers={"Origin": "https://evil.example"})
        self.assertEqual((evil.status_code, list(evil.json())), (403, ["error"]))
        self.assertIn("default-src 'self'", evil.headers["content-security-policy"])
        self.assertEqual(self.client.delete("/api/reactions/like/dq8", headers={"Origin": "null"}).status_code, 403)
        ok = self.client.post("/api/reactions", json={"game_id": "dq8", "type": "like"},
                              headers={"Origin": "http://localhost:8000"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.client.get("/api/me", headers={"Origin": "https://evil.example"}).status_code, 200)
        with mock.patch.object(config, "ALLOWED_ORIGINS", frozenset({"https://gamepick.replit.app"})):
            r = self.client.delete("/api/reactions/like/dq8", headers={"Origin": "https://gamepick.replit.app/"})
            self.assertEqual(r.status_code, 200)
            r = self.client.delete("/api/reactions/like/dq8", headers={"Origin": "http://localhost:8000"})
            self.assertEqual(r.status_code, 403)

    def test_root_health_docs_and_hidden_errors(self):
        root = self.client.get("/", follow_redirects=False)
        self.assertEqual((root.status_code, root.headers["content-type"].split(";")[0]), (200, "text/html"))
        self.assertIn("/static/js/home.js", root.text)
        self.assertEqual(self.client.head("/").status_code, 200)
        self.assertNotIn("set-cookie", root.headers)                                 # 헬스 체크가 사용자를 안 만듦
        health = self.client.get("/api/health").json()
        self.assertEqual(set(health["backup"]), {"enabled", "consecutive_failures", "last_success"})
        self.assertEqual(self.client.get("/docs").status_code, 404)                  # DEBUG=false
        self.assertEqual(self.client.get("/openapi.json").status_code, 404)
        quiet = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(quiet.close)
        with mock.patch.object(backup.status, "public", side_effect=RuntimeError(r"C:\secret\app.py line 3")), \
                self.assertLogs("uvicorn.error", "ERROR"):
            r = quiet.get("/api/health")
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("secret", r.text)

    def startup(self, **overrides):
        values = {"DB_PATH": Path(self.tmp.name) / "boot" / "gamepick.db", "SEED_DB_PATH": self.db_path,
                  "STORE_SYNC": False, "ICON_FETCH": False, "BACKUP": False, **overrides}
        patches = [mock.patch.object(config, k, v) for k, v in values.items()]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return values

    def test_backup_true_without_library_fails_startup(self):
        self.startup(BACKUP=True)
        with mock.patch.dict(sys.modules, {"replit": None, "replit.object_storage": None}):
            with self.assertRaises(backup.BackupUnavailable):
                with TestClient(app):
                    pass

    def test_startup_without_backup_uses_seed_when_db_missing(self):
        values = self.startup()
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/health").json()["backup"]["enabled"], False)
        self.assertEqual(games_in(values["DB_PATH"]), games_in(self.db_path))

    def test_startup_with_backup_restores_and_final_backup(self):
        values = self.startup(BACKUP=True)
        storage = FakeStorage()
        backup.status.reset()
        self.addCleanup(backup.status.reset)
        with mock.patch.object(backup, "ReplitStorage", return_value=storage), \
                mock.patch.object(backup.Runner, "icon_work"):
            with TestClient(app) as client:
                self.assertTrue(client.get("/api/health").json()["backup"]["enabled"])
        self.assertTrue(values["DB_PATH"].exists())                                  # seed로 시작
        self.assertIn("gamepick-latest.db", storage.objects)                         # 꺼질 때 백업


if __name__ == "__main__":
    unittest.main()

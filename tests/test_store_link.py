"""
게임 상세 스토어 링크 (iOS 기기가 구글 플레이로 가던 문제).
- static/js/store.js의 기기별 분기를 node로 직접 실행해 확인 (node가 없으면 건너뜀)
- scripts/backfill_ios.py: 스토어에는 접속하지 않고 가짜 응답으로 확인
실행: python -m unittest discover tests   (프로젝트 최상위에서)
"""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import backfill_ios  # noqa: E402
from tests.test_api import ApiTestCase  # noqa: E402

STORE_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "store.js"
NODE = shutil.which("node")

IPHONE = {"userAgent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)", "platform": "iPhone", "maxTouchPoints": 5}
IPAD_DESKTOP = {"userAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15",
                "platform": "MacIntel", "maxTouchPoints": 5}                       # iPadOS 13+ 기본 설정
MAC = {"userAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15",
       "platform": "MacIntel", "maxTouchPoints": 0}
ANDROID = {"userAgent": "Mozilla/5.0 (Linux; Android 14; SM-S918N)", "platform": "Linux armv8l", "maxTouchPoints": 5}
WINDOWS = {"userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "platform": "Win32", "maxTouchPoints": 0}

BOTH = {"android_url": "https://play/x", "ios_url": "https://apple/x"}
ANDROID_ONLY = {"android_url": "https://play/x", "ios_url": None}
IOS_ONLY = {"android_url": "", "ios_url": "https://apple/x"}


@unittest.skipUnless(NODE, "node가 없어 store.js 확인을 건너뜀")
class StoreUrlTest(unittest.TestCase):
    def run_js(self, cases):
        """[(nav, game)] → [[platform, url, message]]"""
        script = (f"import {{ platformOf, storeUrl, missingStoreMessage }} from {json.dumps(STORE_JS.as_uri())};\n"
                  f"const cases = {json.dumps(cases)};\n"
                  "console.log(JSON.stringify(cases.map(([nav, game]) => "
                  "[platformOf(nav), storeUrl(game, nav), missingStoreMessage(nav)])));")
        out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                             encoding="utf-8", timeout=30, check=True)
        return json.loads(out.stdout)

    def test_ios_goes_to_app_store_only(self):
        result = self.run_js([(IPHONE, BOTH), (IPHONE, ANDROID_ONLY), (IPAD_DESKTOP, BOTH), (IPAD_DESKTOP, ANDROID_ONLY)])
        self.assertEqual(result, [
            ["ios", "https://apple/x", "App Store 링크 준비 중이에요"],
            ["ios", None, "App Store 링크 준비 중이에요"],               # 구글 플레이로 보내지 않음
            ["ios", "https://apple/x", "App Store 링크 준비 중이에요"],  # 데스크톱 모드 iPad도 iOS
            ["ios", None, "App Store 링크 준비 중이에요"],
        ])

    def test_android_goes_to_play_only(self):
        result = self.run_js([(ANDROID, BOTH), (ANDROID, IOS_ONLY)])
        self.assertEqual(result, [["android", "https://play/x", "Google Play 링크 준비 중이에요"],
                                  ["android", None, "Google Play 링크 준비 중이에요"]])

    def test_pc_prefers_play_then_app_store(self):
        result = self.run_js([(WINDOWS, BOTH), (WINDOWS, IOS_ONLY), (MAC, IOS_ONLY), (WINDOWS, {})])
        self.assertEqual([r[:2] for r in result], [["pc", "https://play/x"], ["pc", "https://apple/x"],
                                                   ["pc", "https://apple/x"], ["pc", None]])  # 터치 없는 Mac은 PC
        self.assertEqual(result[3][2], "스토어 링크 준비 중이에요")


def ios_app(track_id, name, artist, genre=6014):
    return {"trackId": track_id, "trackName": name, "artistName": artist, "sellerName": artist,
            "primaryGenreId": genre, "genreIds": [str(genre)]}


class FakeItunes:
    def __init__(self):
        self.bundles = {"com.dq8": [ios_app(111, "DRAGON QUEST VIII", "SQUARE ENIX")]}
        self.searches = {
            "파이널 판타지 VI": [ios_app(222, "FINAL FANTASY VI", "SQUARE ENIX Co., Ltd."),
                                ios_app(223, "FF 계산기", "SQUARE ENIX", genre=6002)],         # 게임 아님 → 제외
            "크로노 트리거": [ios_app(331, "CHRONO TRIGGER", "SQUARE ENIX"),
                             ios_app(332, "CHRONO CROSS", "SQUARE ENIX")],                      # 둘 다 이름 안 비슷
            "던전앤파이터 모바일": [ios_app(444, "던전앤파이터 모바일", "Other Corp")],          # 개발사 다름
            "메이플스토리M": [ios_app(555, "메이플스토리M", "NEXON Company")],                  # 이미 쓰는 id
        }
        self.calls = []

    def ios_bundle(self, bundle_id):
        self.calls.append(("bundle", bundle_id))
        if bundle_id == "com.boom":
            raise ConnectionError("요청 실패")
        return self.bundles.get(bundle_id, [])

    def ios_search(self, term, n=10):
        self.calls.append(("search", term))
        return self.searches.get(term, [])


class BackfillIosTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        conn = self.db()
        conn.execute("UPDATE games SET android_url = NULL, ios_url = NULL, ios_id = NULL")
        targets = {"dq8": "com.dq8", "ff6": "com.ff6", "chrono": "com.chrono", "dnfm": "com.dnfm",
                   "maplem": "com.maplem", "lineagem": "com.boom", "odin": "com.odin"}
        for game_id, package in targets.items():
            conn.execute("UPDATE games SET android_id = ?, android_url = 'https://play/' || ? WHERE id = ?",
                         (package, package, game_id))
        conn.execute("UPDATE games SET name = '파이널 판타지 VI', developer = 'Square Enix' WHERE id = 'ff6'")
        conn.execute("UPDATE games SET name = '크로노 트리거', developer = 'SQUARE ENIX' WHERE id = 'chrono'")
        conn.execute("UPDATE games SET name = '던전앤파이터 모바일', developer = 'NEXON' WHERE id = 'dnfm'")
        conn.execute("UPDATE games SET name = '메이플스토리M', developer = 'NEXON Company' WHERE id = 'maplem'")
        conn.execute("UPDATE games SET ios_id = '555', ios_url = 'https://apple/555' WHERE id = 'genshin'")
        conn.execute("UPDATE games SET review_status = 'pending' WHERE id = 'odin'")              # 대상 아님
        conn.commit()
        self.conn = conn

    def run_backfill(self, apply):
        return backfill_ios.backfill(FakeItunes(), self.db_path, apply=apply)

    def test_dry_run_reports_without_changing_db(self):
        report = self.run_backfill(apply=False)
        self.assertEqual([(g, i, via) for g, i, via, _ in report["confirmed"]],
                         [("dq8", "111", "bundleId"), ("ff6", "222", "search")])
        self.assertEqual(sorted(g for g, _, _ in report["ambiguous"]), ["chrono", "dnfm", "maplem"])
        self.assertEqual([g for g, _ in report["failed"]], ["lineagem"])             # 하나 실패해도 계속
        self.assertNotIn("odin", json.dumps(report))                                   # 검수 대기는 대상 아님
        self.assertIsNone(self.conn.execute("SELECT ios_url FROM games WHERE id = 'dq8'").fetchone()[0])

    def test_apply_saves_confirmed_only(self):
        self.run_backfill(apply=True)
        rows = {r["id"]: (r["ios_id"], r["ios_url"]) for r in
                self.conn.execute("SELECT id, ios_id, ios_url FROM games WHERE id IN ('dq8','ff6','chrono','dnfm','maplem')")}
        self.assertEqual(rows["dq8"], ("111", "https://apps.apple.com/kr/app/id111"))
        self.assertEqual(rows["ff6"], ("222", "https://apps.apple.com/kr/app/id222"))
        self.assertEqual({k: v for k, v in rows.items() if k not in ("dq8", "ff6")},
                         {"chrono": (None, None), "dnfm": (None, None), "maplem": (None, None)})
        # 다시 돌리면 이미 채운 게임은 대상에서 빠짐
        again = self.run_backfill(apply=True)
        self.assertEqual(again["confirmed"], [])

    def test_name_breaks_tie_between_same_developer_games(self):
        itunes = FakeItunes()
        itunes.searches["크로노 트리거"].append(ios_app(333, "크로노 트리거", "SQUARE ENIX"))
        report = backfill_ios.backfill(itunes, self.db_path)
        self.assertIn(("chrono", "333", "search+name"), [(g, i, via) for g, i, via, _ in report["confirmed"]])


if __name__ == "__main__":
    unittest.main()

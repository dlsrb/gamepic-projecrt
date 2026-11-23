"""
스토어 링크 점검 (PROJECT.md 4.11 C12-k). 모든 게임의 android_url·ios_url이 한국 스토어에서 열리는지 확인.

- Google Play: 링크의 id=패키지로 google-play-scraper app(country='kr') 조회 → 없음(NotFound)이면 죽은 링크
- App Store: 링크의 id 숫자로 lookup?id=&country=kr (100개씩 묶어서) → 결과에 없으면 죽은 링크
- 요청 사이 1초 (sync_store.StoreClient). 네트워크 오류 등으로 확인 못 한 것은 "확인 실패"로 따로 — 죽은 것으로 치지 않음
- 기본은 출력만. --apply면 죽은 링크만 비움(NULL) → 화면은 다른 스토어(PC: android_url || ios_url)나 "준비 중"
  (android_id·ios_id는 남김 → 스토어에 다시 올라오면 자동 수집이 링크를 다시 채움)

실행: python scripts/check_store_links.py            출력만
      python scripts/check_store_links.py --apply    죽은 링크 비우기
"""
import argparse
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.config import DB_PATH  # noqa: E402
from app.db import get_connection  # noqa: E402

PLAY_ID = re.compile(r"[?&]id=([\w.]+)")
IOS_ID = re.compile(r"/id(\d+)")


def check_links(conn, client, apply: bool = False) -> dict:
    """{checked_play, checked_ios, dead_play: [(id, name, url)], dead_ios: [...], unknown: [(id, 스토어, 이유)]}"""
    rows = conn.execute("SELECT id, name, android_url, ios_url FROM games ORDER BY rowid").fetchall()
    report = {"checked_play": 0, "checked_ios": 0, "dead_play": [], "dead_ios": [], "unknown": []}

    for row in rows:
        if not row["android_url"]:
            continue
        match = PLAY_ID.search(row["android_url"])
        if not match:
            report["dead_play"].append((row["id"], row["name"], row["android_url"]))
            continue
        report["checked_play"] += 1
        try:
            info = client.play_details(match.group(1))   # 없으면 None (NotFound)
        except Exception as error:  # noqa: BLE001 - 확인 못 한 것은 죽은 것으로 치지 않음
            report["unknown"].append((row["id"], "play", str(error)[:80]))
            continue
        if info is None:
            report["dead_play"].append((row["id"], row["name"], row["android_url"]))

    ios_rows = [(row, IOS_ID.search(row["ios_url"])) for row in rows if row["ios_url"]]
    for row, match in ios_rows:
        if not match:
            report["dead_ios"].append((row["id"], row["name"], row["ios_url"]))
    wanted = [(row, match.group(1)) for row, match in ios_rows if match]
    report["checked_ios"] = len(wanted)
    try:
        found = client.ios_lookup([ios_id for _, ios_id in wanted])  # {trackId: info}, 100개씩
    except Exception as error:  # noqa: BLE001
        found = None
        report["unknown"] += [(row["id"], "ios", str(error)[:80]) for row, _ in wanted]
    if found is not None:
        report["dead_ios"] += [(row["id"], row["name"], row["ios_url"]) for row, ios_id in wanted if ios_id not in found]

    if apply:
        conn.executemany("UPDATE games SET android_url = NULL WHERE id = ?", [(g,) for g, _, _ in report["dead_play"]])
        conn.executemany("UPDATE games SET ios_url = NULL WHERE id = ?", [(g,) for g, _, _ in report["dead_ios"]])
        conn.commit()
    return report


def main(argv=None):
    from scripts.sync_store import StoreClient

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="스토어 링크 점검")
    parser.add_argument("--apply", action="store_true", help="죽은 링크를 비움 (없으면 출력만)")
    args = parser.parse_args(argv)
    with get_connection(DB_PATH) as conn:
        report = check_links(conn, StoreClient(), apply=args.apply)
    print(f"Google Play {report['checked_play']}개 확인 · 죽은 링크 {len(report['dead_play'])}")
    for game_id, name, url in report["dead_play"]:
        print(f"  {game_id} {name} {url}")
    print(f"App Store {report['checked_ios']}개 확인 · 죽은 링크 {len(report['dead_ios'])}")
    for game_id, name, url in report["dead_ios"]:
        print(f"  {game_id} {name} {url}")
    if report["unknown"]:
        print(f"확인 실패 {len(report['unknown'])} (안 비움)")
        for game_id, store, reason in report["unknown"]:
            print(f"  {game_id} [{store}] {reason}")
    print("죽은 링크를 비웠음" if args.apply else "출력만 (--apply로 죽은 링크 비우기)")


if __name__ == "__main__":
    main()

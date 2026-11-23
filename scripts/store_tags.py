"""
기존 자동 수집 게임에 Google Play 태그 칩 더하기 (PROJECT.md 4.11 C12-j). 한 번 돌리는 용도.
새로 수집되는 게임은 scripts/sync_store.py가 수집할 때 같은 규칙으로 처리한다.

대상: tag_source = 'auto' (AI 초안·사람이 붙인 ai·human 게임은 건드리지 않음), 화면에 보이는 게임, Android ID가 있는 게임
  (iOS만 있는 게임은 App Store에 태그 칩이 없어 대상 아님)
방법: Play app()의 categories 중 id 없는 항목(태그 칩) → data/store_tag_map.json → 우리 태그를 **더하기만** (지우지 않음, 장르 안 바꿈)
      대응되는 칩이 하나라도 있으면 tag_source = 'store'. tags_checked는 그대로(0)
요청 사이 1초 (StoreClient), 실패한 게임은 건너뜀

실행: python scripts/store_tags.py            찾기만 하고 결과 출력 (DB 안 바꿈)
      python scripts/store_tags.py --apply    DB에 반영
"""
import argparse
import collections
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.config import DB_PATH  # noqa: E402
from app.db import get_connection  # noqa: E402
from scripts.sync_store import chip_tags, load_json, play_chips  # noqa: E402

TARGET_SQL = ("SELECT id, name, android_id FROM games WHERE tag_source = 'auto' AND review_status = 'ok' "
              "AND android_id IS NOT NULL ORDER BY rowid")


def apply_store_tags(conn, client, apply: bool = False) -> dict:
    """{targets, fetched, failed: [id], with_chips, marked: [id], added: Counter(태그), unmapped: Counter(칩)}"""
    chip_map = load_json("store_tag_map.json")
    report = {"targets": 0, "fetched": 0, "failed": [], "with_chips": 0, "marked": [],
              "added": collections.Counter(), "unmapped": collections.Counter()}
    games = conn.execute(TARGET_SQL).fetchall()
    report["targets"] = len(games)
    for game in games:
        try:
            info = client.play_details(game["android_id"])
        except Exception:  # noqa: BLE001 - 한 게임 실패로 멈추지 않음
            info = None
        if not info:
            report["failed"].append(game["id"])
            continue
        report["fetched"] += 1
        chips = play_chips(info)
        if chips:
            report["with_chips"] += 1
        report["unmapped"].update(c for c in chips if c not in chip_map)
        features = chip_tags(chips, chip_map)
        if not features:
            continue
        have = {f"{r['category']}:{r['name']}" for r in conn.execute(
            "SELECT t.category, t.name FROM game_tags gt JOIN tags t ON t.id = gt.tag_id WHERE gt.game_id = ?",
            (game["id"],))}
        new = [f for f in features if f not in have]
        report["added"].update(new)
        report["marked"].append(game["id"])
        if apply:
            for feature in new:
                category, name = feature.split(":", 1)
                conn.execute("INSERT OR IGNORE INTO game_tags (game_id, tag_id, auto) "
                             "SELECT ?, id, 1 FROM tags WHERE category = ? AND name = ?", (game["id"], category, name))
            conn.execute("UPDATE games SET tag_source = 'store' WHERE id = ? AND tag_source = 'auto'", (game["id"],))
    if apply:
        conn.commit()
    return report


def main(argv=None):
    from scripts.sync_store import StoreClient

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="기존 자동 수집 게임에 Play 태그 칩 더하기")
    parser.add_argument("--apply", action="store_true", help="DB에 반영 (없으면 출력만)")
    args = parser.parse_args(argv)
    with get_connection(DB_PATH) as conn:
        report = apply_store_tags(conn, StoreClient(), apply=args.apply)
    print(f"대상 {report['targets']}개 (tag_source=auto, Android 있음) · 받음 {report['fetched']} · 실패 {len(report['failed'])}")
    print(f"태그 칩이 온 게임 {report['with_chips']} · 대응 칩이 있어 store로 바뀌는 게임 {len(report['marked'])}"
          + (" — DB에 반영함" if args.apply else " — 출력만 (--apply로 반영)"))
    print("더하는 태그:", ", ".join(f"{k} {n}" for k, n in report["added"].most_common()) or "없음")
    print("대응표에 없는 칩(버림, 많은 순 15개):", ", ".join(f"{k} {n}" for k, n in report["unmapped"].most_common(15)))
    if report["failed"]:
        print("실패:", ", ".join(report["failed"]))


if __name__ == "__main__":
    main()

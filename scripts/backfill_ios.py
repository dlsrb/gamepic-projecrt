"""
빠진 App Store 링크 채우기 (게임 상세에서 iOS 기기가 구글 플레이로 가던 문제의 데이터 쪽 수정).

대상: review_status='ok'이고 android_url은 있는데 ios_url이 빈 게임.
1. iTunes lookup?bundleId=<android_id>&country=kr — 같은 패키지 이름의 게임이 있으면 확정
2. 없으면 search?term=<게임명>&country=kr&entity=software — 게임이면서 개발사(artistName·sellerName)가
   우리 developer와 같은 것만 (sync_store.same_developer 규칙). 그런 결과가 하나면 확정,
   여러 개면 이름 유사도 0.6 이상이면서 가장 비슷한 것이 하나일 때만 확정
3. 확정된 것만 ios_id, ios_url(APPSTORE_URL 형식) 저장. 애매한 것·못 찾은 것은 목록만 출력
   이미 다른 게임이 쓰는 ios_id면 저장하지 않고 애매함으로 돌림. 요청 사이 1초 (StoreClient)

실행: python scripts/backfill_ios.py            찾기만 하고 결과 출력 (DB 안 바꿈)
      python scripts/backfill_ios.py --apply    확정된 것을 DB에 저장
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.config import DB_PATH  # noqa: E402
from app.db import get_connection  # noqa: E402
from scripts.fetch_game_meta import APPSTORE_URL, similarity  # noqa: E402
from scripts.sync_store import IOS_GAME_GENRE, NAME_MATCH, same_developer  # noqa: E402

TARGET_SQL = """
    SELECT id, name, developer, android_id FROM games
    WHERE review_status = 'ok' AND COALESCE(android_url, '') != '' AND COALESCE(ios_url, '') = ''
    ORDER BY rowid
"""


def is_game(info: dict) -> bool:
    return info.get("primaryGenreId") == IOS_GAME_GENRE or IOS_GAME_GENRE in [int(g) for g in info.get("genreIds", [])
                                                                               if str(g).isdigit()]


def label(info: dict) -> str:
    return f"{info.get('trackName')} / {info.get('artistName')} (id{info.get('trackId')})"


def find(client, game: dict) -> dict:
    """{status: confirmed|ambiguous|missing, info?, via?, candidates: [설명]}"""
    if game["android_id"]:
        hits = [r for r in client.ios_bundle(game["android_id"]) if r.get("trackId") and is_game(r)]
        if len(hits) == 1:
            return {"status": "confirmed", "info": hits[0], "via": "bundleId", "candidates": []}

    results = [r for r in client.ios_search(game["name"]) if r.get("trackId") and is_game(r)]
    same_dev = [r for r in results
                if same_developer(r.get("artistName"), game["developer"])
                or same_developer(r.get("sellerName"), game["developer"])]
    if len(same_dev) == 1:
        return {"status": "confirmed", "info": same_dev[0], "via": "search", "candidates": []}
    if len(same_dev) > 1:
        scored = sorted(((similarity(game["name"], r.get("trackName") or ""), r) for r in same_dev),
                        key=lambda x: x[0], reverse=True)
        if scored[0][0] >= NAME_MATCH and scored[0][0] > scored[1][0]:
            return {"status": "confirmed", "info": scored[0][1], "via": "search+name", "candidates": []}
        return {"status": "ambiguous", "candidates": [label(r) for _, r in scored]}
    if results:  # 게임은 나왔지만 개발사가 다름 → 사람이 확인
        return {"status": "ambiguous", "candidates": ["개발사 다름: " + label(r) for r in results[:3]]}
    return {"status": "missing", "candidates": []}


def backfill(client, db_path=None, apply: bool = False) -> dict:
    """{confirmed: [(id, ios_id, via)], ambiguous: [(id, name, candidates)], missing: [(id, name)], failed: [(id, error)]}"""
    report = {"confirmed": [], "ambiguous": [], "missing": [], "failed": []}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_connection(db_path or DB_PATH) as conn:
        games = [dict(r) for r in conn.execute(TARGET_SQL)]
        used = {r[0]: r[1] for r in conn.execute("SELECT ios_id, id FROM games WHERE COALESCE(ios_id, '') != ''")}
        for game in games:
            try:
                result = find(client, game)
            except Exception as error:  # noqa: BLE001 - 한 게임 실패로 멈추지 않음
                report["failed"].append((game["id"], str(error)))
                continue
            if result["status"] == "confirmed":
                ios_id = str(result["info"]["trackId"])
                owner = used.get(ios_id)
                if owner and owner != game["id"]:
                    report["ambiguous"].append((game["id"], game["name"],
                                                [f"이미 {owner} 게임이 쓰는 id: " + label(result["info"])]))
                    continue
                used[ios_id] = game["id"]
                report["confirmed"].append((game["id"], ios_id, result["via"], label(result["info"])))
                if apply:
                    conn.execute("UPDATE games SET ios_id = ?, ios_url = ?, updated_at = ? WHERE id = ?",
                                 (ios_id, APPSTORE_URL.format(ios_id), now, game["id"]))
            elif result["status"] == "ambiguous":
                report["ambiguous"].append((game["id"], game["name"], result["candidates"]))
            else:
                report["missing"].append((game["id"], game["name"]))
        if apply:
            conn.commit()
    return report


def main():
    from scripts.sync_store import StoreClient

    parser = argparse.ArgumentParser(description="빠진 App Store 링크 채우기")
    parser.add_argument("--apply", action="store_true", help="확정된 것을 DB에 저장 (없으면 출력만)")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    report = backfill(StoreClient(), apply=args.apply)
    print(f"[확정 {len(report['confirmed'])}개]" + (" — DB에 저장함" if args.apply else " — 출력만 (--apply로 저장)"))
    for game_id, ios_id, via, text in report["confirmed"]:
        print(f"  {game_id} → {ios_id} ({via}) {text}")
    print(f"[애매함 {len(report['ambiguous'])}개] — 저장 안 함, 직접 확인")
    for game_id, name, candidates in report["ambiguous"]:
        print(f"  {game_id} {name}")
        for c in candidates:
            print(f"      {c}")
    print(f"[못 찾음 {len(report['missing'])}개]")
    for game_id, name in report["missing"]:
        print(f"  {game_id} {name}")
    if report["failed"]:
        print(f"[요청 실패 {len(report['failed'])}개]")
        for game_id, error in report["failed"]:
            print(f"  {game_id}: {error}")


if __name__ == "__main__":
    main()

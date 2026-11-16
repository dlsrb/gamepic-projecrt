"""
게임 정보 채우기 (PROJECT.md 4.11 C7-a1).
data/game_list.csv의 android_id로 Google Play(ko/kr)에서 정보를 가져와 DB games에 저장한다.
- 아이콘 → static/img/games/{game_id}.png 로 내려받음 (외부 이미지 주소를 쓰지 않아 CSP 그대로)
- 짧은 설명·개발사·설치 수·별점·별점 수·스토어 링크(Google Play, ios_id 있으면 App Store)
- 스토어의 사용자 리뷰 글은 가져오지 않는다 (숫자만)
- DB에 있는 게임만 채운다. 게임마다 실제로 가져온 앱 이름을 출력해서 잘못된 ID를 바로 알아보게 한다
- 여러 번 실행해도 안전 (같은 값으로 덮어씀), 요청 사이 1초 쉼, 실패한 게임은 건너뛰고 마지막에 목록 출력

실행: python scripts/fetch_game_meta.py            (먼저 python scripts/init_db.py)
      python scripts/fetch_game_meta.py dq8 brawl  (일부 게임만)
"""
import csv
import re
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import requests  # noqa: E402
from google_play_scraper import app as play_app  # noqa: E402

from app.config import DB_PATH, STATIC_DIR  # noqa: E402
from app.db import get_connection  # noqa: E402

GAME_LIST = BASE_DIR / "data" / "game_list.csv"
ICON_DIR = STATIC_DIR / "img" / "games"
ICON_SIZE = 256                    # 아이콘 크기(px). Google 이미지 주소 끝에 =s256을 붙여 요청
REQUEST_GAP_SECONDS = 1.0
TIMEOUT_SECONDS = 15
NAME_MATCH_WARN = 0.5              # 우리 이름과 스토어 이름 유사도가 이보다 낮으면 ⚠ 표시
CONTAIN_RATIO = 0.7                # 한쪽 이름이 다른 쪽에 들어 있을 때 같은 것으로 보는 최소 길이 비율
PLAY_URL = "https://play.google.com/store/apps/details?id={}"
APPSTORE_URL = "https://apps.apple.com/kr/app/id{}"
APPSTORE_LOOKUP = "https://itunes.apple.com/lookup"   # 공개 조회 API (앱 이름 확인용)
IMAGE_SIGNATURES = {b"\x89PNG": "png", b"\xff\xd8\xff": "jpeg", b"RIFF": "webp"}


def similarity(a: str, b: str) -> float:
    clean = lambda s: re.sub(r"[\W_]+", "", s).lower()  # noqa: E731
    a, b = clean(a), clean(b)
    if not a or not b:
        return 0.0
    short, long_ = sorted((a, b), key=len)
    # "던전앤파이터모바일" ⊂ "던전앤파이터모바일12"처럼 한쪽이 다른 쪽에 거의 다 들어 있으면 같은 것으로.
    # 길이 차이가 크면(마인크래프트 ⊂ 플래닛크래프트마인크래프트건설…) 다른 앱일 수 있어 일반 비교로
    if short in long_ and len(short) / len(long_) >= CONTAIN_RATIO:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def load_list() -> dict[str, dict]:
    with open(GAME_LIST, encoding="utf-8-sig") as f:
        return {row["game_id"]: row for row in csv.DictReader(f)}


def download_icon(url: str, game_id: str) -> str:
    """아이콘을 내려받아 저장하고 화면에서 쓸 주소를 돌려준다."""
    response = requests.get(f"{url}=s{ICON_SIZE}", timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    kind = next((k for sig, k in IMAGE_SIGNATURES.items() if response.content.startswith(sig)), None)
    if kind is None:
        raise ValueError("이미지가 아닌 응답")
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    (ICON_DIR / f"{game_id}.png").write_bytes(response.content)
    return f"/static/img/games/{game_id}.png"


def ios_title(ios_id: str) -> str | None:
    """App Store 앱 이름. 없는 ID면 None (그때는 App Store 링크를 저장하지 않음)."""
    response = requests.get(APPSTORE_LOOKUP, params={"id": ios_id, "country": "kr"}, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    results = response.json().get("results", [])
    return results[0].get("trackName") if results else None


def fetch_one(conn, game_id: str, name: str, row: dict) -> str:
    """게임 하나를 채우고 출력할 한 줄(들)을 돌려준다."""
    info = play_app(row["android_id"], lang="ko", country="kr")
    title = info.get("title") or ""
    icon = download_icon(info["icon"], game_id) if info.get("icon") else None
    ios_id = (row.get("ios_id") or "").strip()
    ios_name = ios_title(ios_id) if ios_id.isdigit() else None
    if not ios_name:
        ios_id = ""
    conn.execute(
        """
        UPDATE games SET thumbnail_url = ?, description = ?, developer = ?, installs = ?,
               store_rating = ?, store_rating_count = ?, android_url = ?, ios_url = ?, android_id = ?, ios_id = ?
        WHERE id = ?
        """,
        (icon, info.get("summary") or None, info.get("developer") or None, info.get("minInstalls"),
         round(info["score"], 2) if info.get("score") else None, info.get("ratings") or None,
         PLAY_URL.format(row["android_id"]), APPSTORE_URL.format(ios_id) if ios_id.isdigit() else None,
         row["android_id"], ios_id if ios_id.isdigit() else None, game_id),
    )
    warn = lambda store_name: "⚠ 이름 다름" if similarity(name, store_name) < NAME_MATCH_WARN else "  "  # noqa: E731
    lines = f"{warn(title)} {game_id:14} {name} → Android: {title} ({info.get('developer')}, {info.get('installs')})"
    if ios_name:
        lines += f"\n{warn(ios_name)} {'':14} {'':{len(name)}} → iOS: {ios_name}"
    elif (row.get("ios_id") or "").strip():
        lines += f"\n✗  {'':14} iOS ID {row['ios_id']}: App Store에 없음 → App Store 링크 비워 둠"
    return lines


def main(only: list[str]) -> None:
    if not DB_PATH.exists():
        sys.exit("DB가 없습니다. 먼저 python scripts/init_db.py 를 실행하세요.")
    listed = load_list()
    with get_connection() as conn:
        db_games = [(r["id"], r["name"]) for r in conn.execute("SELECT id, name FROM games ORDER BY rowid")]
    targets = [(gid, name) for gid, name in db_games if not only or gid in only]

    filled, skipped, failed = [], [], []
    for i, (game_id, name) in enumerate(targets):
        row = listed.get(game_id)
        if not row or not row.get("android_id"):
            skipped.append(game_id)
            continue
        if i:
            time.sleep(REQUEST_GAP_SECONDS)
        try:
            with get_connection() as conn:  # 게임마다 저장 (중간에 실패해도 앞의 결과는 남음)
                line = fetch_one(conn, game_id, name, row)
            print(line)
            filled.append(game_id)
        except Exception as error:  # 스토어 응답 오류·없는 앱 등 → 건너뛰고 계속
            print(f"✗  {game_id:14} {name} ({row['android_id']}): {error}")
            failed.append(game_id)

    print(f"\n채움 {len(filled)}개 / 목록에 없어 비워 둠 {len(skipped)}개 / 실패 {len(failed)}개")
    if skipped:
        print("  비워 둠:", ", ".join(skipped))
    if failed:
        print("  실패:", ", ".join(failed))


if __name__ == "__main__":
    main(sys.argv[1:])

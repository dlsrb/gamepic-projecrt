"""
게임 아이콘 다시 받기 (PROJECT.md 4.11 C10, docs/C10_배포_지시.md 1단계 4)).

아이콘(static/img/games/)은 저작권 때문에 저장소·배포 묶음에 넣지 않는다. 그래서 배포 서버에는 처음에 아이콘 파일이 없다.
서버가 켜질 때 DB에 아이콘 주소(/static/img/games/{id}.png)가 있는데 파일이 없는 게임을 찾아
별도 스레드에서 스토어에서 다시 받는다 (요청 간격 1초, scripts/sync_store.py의 StoreClient 재사용).
받는 동안 화면은 기존 대체 표시(첫 글자 카드). .env의 ICON_FETCH=false면 끔.
"""
import logging
import threading

from app import config
from app.db import get_connection

ICON_PREFIX = "/static/img/games/"
IMAGE_SIGNATURES = (b"\x89PNG", b"\xff\xd8\xff", b"RIFF")

log = logging.getLogger("uvicorn.error")


def icon_path(game_id: str):
    return config.STATIC_DIR / "img" / "games" / f"{game_id}.png"


def missing_icons(conn) -> list[dict]:
    """아이콘 주소는 있는데 파일이 없는 게임 [{id, android_id, ios_id}]"""
    rows = conn.execute("SELECT id, android_id, ios_id, thumbnail_url FROM games "
                        "WHERE thumbnail_url LIKE ? ORDER BY rowid", (ICON_PREFIX + "%",)).fetchall()
    return [{"id": r["id"], "android_id": r["android_id"], "ios_id": r["ios_id"]}
            for r in rows if r["thumbnail_url"] == f"{ICON_PREFIX}{r['id']}.png" and not icon_path(r["id"]).exists()]


def icon_url(client, game: dict) -> str | None:
    """스토어에서 아이콘 원본 주소 찾기: Android(Play) 먼저, 없으면 App Store"""
    from scripts.sync_store import from_ios, from_play

    if game["android_id"]:
        info = client.play_details(game["android_id"])
        if info and info.get("icon"):
            return from_play(game["android_id"], info)["icon_url"]
    if game["ios_id"]:
        info = client.ios_lookup([game["ios_id"]]).get(str(game["ios_id"]))
        if info and info.get("artworkUrl512"):
            return from_ios(info)["icon_url"]
    return None


def refill(client=None, db_path=None) -> dict:
    """없는 아이콘을 하나씩 받는다. 게임 하나가 실패해도 계속. {missing, saved, failed}"""
    if client is None:
        from scripts.sync_store import StoreClient

        client = StoreClient()
    with get_connection(db_path or config.DB_PATH) as conn:
        games = missing_icons(conn)
    saved, failed = 0, []
    for game in games:
        try:
            url = icon_url(client, game)
            if not url:
                raise ValueError("스토어에 아이콘 없음")
            content = client.image(url)
            if not content.startswith(IMAGE_SIGNATURES):
                raise ValueError("이미지가 아닌 응답")
            path = icon_path(game["id"])
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".part")
            tmp.write_bytes(content)
            tmp.replace(path)  # 다 받은 뒤에만 보이게
            saved += 1
        except Exception as error:  # noqa: BLE001 - 아이콘 하나 실패로 멈추지 않음 (화면은 대체 표시)
            failed.append(game["id"])
            log.warning("아이콘 받기 실패 %s: %s", game["id"], error)
    return {"missing": len(games), "saved": saved, "failed": failed}


def start_refill_thread() -> threading.Thread | None:
    """서버 시작 때 호출. 받을 게 없으면 스레드를 만들지 않음"""
    if not config.ICON_FETCH:
        return None
    try:
        with get_connection(config.DB_PATH) as conn:
            if not missing_icons(conn):
                return None
    except Exception:  # noqa: BLE001 - DB가 아직 없거나 깨졌어도 서버는 켜짐
        log.exception("아이콘 확인 실패")
        return None

    def work():
        try:
            log.info("아이콘 다시 받기 끝: %s", refill())
        except Exception:  # noqa: BLE001
            log.exception("아이콘 다시 받기 실패")

    thread = threading.Thread(target=work, name="icon-refill", daemon=True)
    thread.start()
    return thread

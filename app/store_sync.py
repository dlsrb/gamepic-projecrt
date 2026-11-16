"""
서버 안에서 스토어 자동 수집을 하루 1번 돌린다 (PROJECT.md 4.11 C7-a2).
- 서버가 켜지고 START_DELAY초 뒤부터 CHECK_EVERY초마다 확인 → 마지막 성공이 24시간보다 오래됐으면 수집 1번
- 수집은 별도 스레드에서 (요청 처리를 막지 않음). 실패해도 서버는 그대로, 다음 확인 때 다시 시도
- .env의 STORE_SYNC=false면 끔 (C10: Reserved VM이라 Replit 예약 실행으로 옮기지 않고 서버 안 스레드 그대로)
- C10부터 app/lifecycle.py가 이 lifespan을 감싸서 씀
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from app import config
from app.db import get_connection

START_DELAY = 60
CHECK_EVERY = 3600
INTERVAL = timedelta(hours=24)

log = logging.getLogger("uvicorn.error")


def due(now: datetime | None = None) -> bool:
    from scripts.sync_store import last_success

    with get_connection() as conn:
        last = last_success(conn)
    return last is None or (now or datetime.now(timezone.utc)) - last >= INTERVAL


def run_once() -> None:
    from scripts.sync_store import run_sync

    report = run_sync()
    log.info("스토어 자동 수집: %s", {k: v for k, v in report.items() if k != "notes"})


async def _loop():
    await asyncio.sleep(START_DELAY)
    while True:
        try:
            if await asyncio.to_thread(due):
                await asyncio.to_thread(run_once)
        except Exception:  # noqa: BLE001 - 수집 실패가 서버를 멈추면 안 됨
            log.exception("스토어 자동 수집 실패 (다음 확인 때 다시 시도)")
        await asyncio.sleep(CHECK_EVERY)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(_loop()) if config.STORE_SYNC else None
    try:
        yield
    finally:
        if task:
            task.cancel()

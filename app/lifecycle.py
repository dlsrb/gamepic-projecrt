"""
서버가 켜지고 꺼질 때 할 일 (FastAPI lifespan). C10에서 store_sync의 lifespan을 감싸 하나로 모음.

켜질 때
1. BACKUP=true: Object Storage 라이브러리를 못 가져오면 **서버 시작 실패** (11/16 결정) → DB 파일이 없으면 latest 복원
   (없으면 seed) → 아이콘은 별도 스레드(Object Storage → 스토어 → 올리기)
   BACKUP=false(로컬): DB 파일이 없을 때만 data/seed.db 복사, 아이콘은 없는 것만 스토어에서
2. 표 구조를 최신으로 (scripts/init_db.py의 표 만들기 부분, 이미 있으면 그대로)
3. 10분마다 백업(BACKUP일 때), 하루 1번 스토어 수집(STORE_SYNC일 때)
꺼질 때: 마지막 백업 한 번
"""
import logging
from contextlib import asynccontextmanager

from app import backup, config, icons, store_sync

log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app):
    runner = None
    if config.BACKUP:
        runner = backup.Runner(backup.ReplitStorage())  # 라이브러리가 없으면 BackupUnavailable → 서버가 안 켜짐
        runner.start()
    else:
        restored = backup.restore_if_missing(None)
        if restored != "kept":
            log.info("DB 파일이 없어서: %s", restored)
        icons.start_refill_thread()
    backup.ensure_schema()
    if runner:
        runner.launch()
    try:
        async with store_sync.lifespan(app):
            yield
    finally:
        if runner:
            await runner.stop()

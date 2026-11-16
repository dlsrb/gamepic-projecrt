"""
DB 백업 수동 명령 (PROJECT.md 4.11 C10). Replit Object Storage를 쓰므로 Replit 안(Python 3.12)에서만 동작.

실행: python scripts/backup_db.py --list               Object Storage에 있는 백업 목록
      python scripts/backup_db.py --now                지금 DB를 바로 백업 (BACKUP=true일 때만 — 작업 화면 DB가
                                                       배포 백업을 덮어쓰지 않게)
      python scripts/backup_db.py --restore 20261120   그날 일별 백업을 latest로 되돌림:
          ① 지금 latest를 gamepick-before-restore-<시각>.db로 먼저 올림 ② 그날 것을 latest로 복사
          ③ 복원 대기 표시를 둠 (지금 켜진 서버가 꺼지면서 latest를 덮어쓰지 않게)
          → 배포 화면에서 "다시 배포"하면 새 서버가 latest로 시작하고 표시를 지움
"""
import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app import backup, config  # noqa: E402


def restore(storage, day: str, now: datetime | None = None) -> str:
    """일별 백업 day(YYYYMMDD)를 latest로. 먼저 올린 before-restore 이름(latest가 없었으면 '')"""
    if not re.fullmatch(r"\d{8}", day):
        raise ValueError("날짜는 YYYYMMDD (예: 20261120)")
    source = f"{backup.DAILY_PREFIX}{day}.db"
    if not storage.exists(source):
        raise FileNotFoundError(f"{source} 가 없어요. --list 로 확인하세요.")
    saved = ""
    if storage.exists(backup.LATEST):
        stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
        saved = f"gamepick-before-restore-{stamp}.db"
        storage.copy(backup.LATEST, saved)
    storage.copy(source, backup.LATEST)
    storage.put_text(backup.RESTORE_MARKER, day)
    return saved


def main(argv=None):
    parser = argparse.ArgumentParser(description="DB 백업 수동 명령 (Replit Object Storage)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--now", action="store_true")
    group.add_argument("--list", action="store_true")
    group.add_argument("--restore", metavar="YYYYMMDD")
    args = parser.parse_args(argv)
    storage = backup.ReplitStorage()

    if args.list:
        for name in sorted(storage.names("gamepick-")):
            print(name)
        print(f"아이콘 {len(storage.names(backup.ICON_PREFIX))}개")
    elif args.now:
        if not config.BACKUP:
            sys.exit("BACKUP=true가 아니에요. 배포 환경에서만 --now를 쓰세요 (작업 화면 DB가 배포 백업을 덮어쓰지 않게).")
        print(backup.backup_once(storage))
    else:
        saved = restore(storage, args.restore)
        print(f"{args.restore} → latest" + (f" (이전 latest는 {saved})" if saved else ""))
        print("배포 화면에서 다시 배포하면 새 서버가 이 백업으로 시작합니다.")


if __name__ == "__main__":
    main()

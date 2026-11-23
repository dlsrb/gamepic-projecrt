"""
태그 검수 도구 (PROJECT.md 4.11 C12-a).

자동 수집 게임(source='auto')의 태그는 스토어 장르·설명 키워드로 붙인 것이라 틀리거나 비어 있을 수 있다.
사람이 확인한 게임만 games.tags_checked = 1 → 설문 맞춤 추천에 나오고, 카드·상세에 태그 칩이 보인다.

실행:
  python scripts/tag_review.py --export [--all] [--top 50]   data/tag_review.csv 로 내보내기 (기본: 검수 안 된 게임만, --top: 앞 N개만)
      정렬 = 홈에 많이 보일 게임 먼저: 스토어 차트 순위(두 스토어 중 높은 쪽) → 설치 수 많은 순
      맨 윗줄은 채우는 방법 안내(# 로 시작, 가져올 때 건너뜀), 그다음 줄이 열 이름
      열: id, name, developer, description(스토어 설명 앞 200자), current_tags, suggested_tags(빈칸), checked(빈칸)
  python scripts/tag_review.py --import [--dry-run] [--source ai]  CSV에서 checked를 채운 줄만 반영
      --source: 태그를 누가 붙였나 → games.tag_source (기본 human = 사람이 검수, ai = AI 초안을 확신도 기준으로 승인, C12-j)
      checked가 1·y·yes·o·true·v 중 하나인 줄:
        suggested_tags가 있으면 → 게임 태그를 그것으로 통째로 바꿈 (장르 정확히 1개 필요)
        suggested_tags가 비었으면 → 지금 태그 그대로 두고 검수 표시만
      태그는 쉼표(,)로 구분 (; 도 됨), '종류:이름' 또는 이름만 (이름은 종류 사이에 겹치지 않음)
      없는 태그 이름·장르가 0개나 2개 이상이면 그 줄은 건너뛰고 이유를 출력
  python scripts/tag_review.py --tag-list            태그표 전체를 docs/tag_list.md로 (채울 때 보기용)
  python scripts/tag_review.py --add ID 태그 [태그 ...]   한 게임에 태그 더하고 검수 표시 (장르를 하나 더 붙일 수는 없음, tag_source=human)
  python scripts/tag_review.py --sources             태그 출처별 게임 수 (auto·store·ai·human, 검수 여부)
제안 태그를 LLM으로 채우는 기능은 아직 없음 (나중에 검토).
"""
import argparse
import csv
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.config import DB_PATH  # noqa: E402
from app.db import get_connection  # noqa: E402

CSV_PATH = BASE_DIR / "data" / "tag_review.csv"
TAG_LIST_PATH = BASE_DIR / "docs" / "tag_list.md"
GUIDE = ("# 채우는 방법: suggested_tags에 태그 이름을 쉼표로 구분해 적기 (태그는 docs/tag_list.md에 있는 이름만, 장르는 꼭 하나), "
         "비우면 지금 태그 그대로. 검수 끝난 줄은 checked에 1. 다 채우면 python scripts/tag_review.py --import")
CATEGORY_LABELS = {"genre": "장르 (꼭 하나)", "subgenre": "세부 장르", "view": "그래픽", "play": "플레이", "mood": "분위기",
                   "bm": "과금"}
FIELDS = ["id", "name", "developer", "description", "current_tags", "suggested_tags", "checked"]
TRUE = {"1", "y", "yes", "o", "true", "v", "✓"}
DESCRIPTION_MAX = 200
SOURCES = ("auto", "store", "ai", "human")  # games.tag_source (C12-j)


class TagError(ValueError):
    """CSV·명령의 태그가 잘못됨"""


def tag_table(conn) -> dict[str, tuple[int, str, str]]:
    """'종류:이름'과 '이름' 둘 다 → (tag_id, 종류, 이름)"""
    table = {}
    for row in conn.execute("SELECT id, category, name FROM tags"):
        value = (row["id"], row["category"], row["name"])
        table[f"{row['category']}:{row['name']}"] = value
        table.setdefault(row["name"], value)
    return table


def parse_tags(text: str, table: dict) -> list[tuple[int, str, str]]:
    words = [w.strip() for w in re.split(r"[;,]", text or "") if w.strip()]
    unknown = [w for w in words if w not in table]
    if unknown:
        raise TagError(f"없는 태그: {', '.join(unknown)}")
    return list(dict.fromkeys(table[w] for w in words))


def current_tags(conn, game_id: str) -> list[str]:
    return [f"{r['category']}:{r['name']}" for r in conn.execute(
        "SELECT t.category, t.name FROM game_tags gt JOIN tags t ON t.id = gt.tag_id WHERE gt.game_id = ? "
        "ORDER BY CASE t.category WHEN 'genre' THEN 0 ELSE 1 END, gt.rowid", (game_id,))]


def export(conn, path: Path = CSV_PATH, include_checked: bool = False, top: int | None = None) -> int:
    rows = conn.execute(
        f"""
        SELECT id, name, developer, description, installs, chart_rank_android, chart_rank_ios FROM games
        WHERE source = 'auto' AND review_status = 'ok' {"" if include_checked else "AND tags_checked = 0"}
        """
    ).fetchall()

    def exposure(row):  # 차트 순위가 있으면 먼저(높은 순위부터), 그다음 설치 수 많은 순
        ranks = [r for r in (row["chart_rank_android"], row["chart_rank_ios"]) if r]
        return (0, min(ranks), 0) if ranks else (1, 0, -(row["installs"] or 0))

    rows = sorted(rows, key=exposure)[:top]  # top=None이면 전부
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig: 엑셀에서 한글이 안 깨지게
        f.write(GUIDE + "\r\n")
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "id": row["id"], "name": row["name"], "developer": row["developer"] or "",
                "description": " ".join((row["description"] or "").split())[:DESCRIPTION_MAX],
                "current_tags": ", ".join(current_tags(conn, row["id"])), "suggested_tags": "", "checked": "",
            })
    return len(rows)


def check_genres(tags: list[tuple[int, str, str]]) -> None:
    genres = [t[2] for t in tags if t[1] == "genre"]
    if len(genres) != 1:
        raise TagError(f"장르는 정확히 1개여야 함 (지금 {len(genres)}개{': ' + ', '.join(genres) if genres else ''})")


def set_tags(conn, game_id: str, tags: list[tuple[int, str, str]]) -> None:
    check_genres(tags)
    conn.execute("DELETE FROM game_tags WHERE game_id = ?", (game_id,))
    conn.executemany("INSERT INTO game_tags (game_id, tag_id, auto) VALUES (?, ?, 0)", [(game_id, t[0]) for t in tags])


def mark_checked(conn, game_id: str, source: str = "human") -> None:
    """검수 표시 + 태그 출처 (C12-j: ai·human 모두 checked)"""
    if source not in SOURCES:
        raise TagError(f"출처는 {', '.join(SOURCES)} 중 하나")
    conn.execute("UPDATE games SET tags_checked = 1, tag_source = ? WHERE id = ?", (source, game_id))


def import_csv(conn, path: Path = CSV_PATH, dry_run: bool = False, source: str = "human") -> dict:
    """{changed: [id], checked_only: [id], skipped: [(id, 이유)]}"""
    table = tag_table(conn)
    known = {r["id"] for r in conn.execute("SELECT id FROM games")}
    report = {"changed": [], "checked_only": [], "skipped": []}
    with path.open(encoding="utf-8-sig", newline="") as f:
        lines = (line for line in f if not line.startswith("#"))  # 맨 위 안내 줄 건너뜀
        for row in csv.DictReader(lines):
            game_id = (row.get("id") or "").strip()
            if (row.get("checked") or "").strip().lower() not in TRUE:
                continue
            if game_id not in known:
                report["skipped"].append((game_id, "없는 게임"))
                continue
            try:
                suggested = (row.get("suggested_tags") or "").strip()
                if suggested:
                    tags = parse_tags(suggested, table)
                    check_genres(tags)
                    if not dry_run:
                        set_tags(conn, game_id, tags)
                    report["changed"].append(game_id)
                else:
                    report["checked_only"].append(game_id)
                if not dry_run:
                    mark_checked(conn, game_id, source)
            except TagError as error:
                report["skipped"].append((game_id, str(error)))
    if not dry_run:
        conn.commit()
    return report


def add_tags(conn, game_id: str, words: list[str]) -> list[str]:
    """태그 더하기 + 검수 표시. 결과 태그 목록"""
    if not conn.execute("SELECT 1 FROM games WHERE id = ?", (game_id,)).fetchone():
        raise TagError(f"없는 게임: {game_id}")
    table = tag_table(conn)
    now = parse_tags("; ".join(current_tags(conn, game_id)), table)
    new = [t for t in parse_tags("; ".join(words), table) if t not in now]
    set_tags(conn, game_id, now + new)  # 장르가 2개가 되면 TagError (한 게임 = 장르 하나)
    mark_checked(conn, game_id)
    conn.commit()
    return current_tags(conn, game_id)


def write_tag_list(conn, path: Path = TAG_LIST_PATH) -> int:
    """태그표 전체를 종류별로 (채울 때 보기용 docs/tag_list.md)"""
    rows = conn.execute("SELECT category, name FROM tags ORDER BY id").fetchall()
    lines = ["# 태그 목록 (자동 생성: `python scripts/tag_review.py --tag-list`)", "",
             "`data/tag_review.csv`의 suggested_tags에는 아래 이름만 쓴다. 쉼표로 구분, 장르는 꼭 하나.", ""]
    for category, label in CATEGORY_LABELS.items():
        names = [r["name"] for r in rows if r["category"] == category]
        if names:
            lines += [f"## {label} ({len(names)}개)", "", ", ".join(names), ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return len(rows)


def source_counts(conn) -> list[tuple[str, int, int]]:
    """[(출처, 게임 수, 그중 검수(checked) 수)] — 화면에 보이는 게임(review_status='ok')만"""
    rows = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT tag_source, COUNT(*), SUM(CASE WHEN source != 'auto' OR tags_checked = 1 THEN 1 ELSE 0 END) "
        "FROM games WHERE review_status = 'ok' GROUP BY tag_source")}
    return [(s, *rows.get(s, (0, 0))) for s in SOURCES]


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="자동 수집 게임 태그 검수")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--export", action="store_true", help=f"{CSV_PATH.name}로 내보내기")
    group.add_argument("--import", dest="import_", action="store_true", help="CSV에서 검수한 줄 반영")
    group.add_argument("--add", nargs="+", metavar=("ID", "TAG"), help="한 게임에 태그 더하고 검수 표시")
    group.add_argument("--tag-list", action="store_true", help=f"태그표를 {TAG_LIST_PATH.name}로")
    group.add_argument("--sources", action="store_true", help="태그 출처별 게임 수")
    parser.add_argument("--all", action="store_true", help="--export: 이미 검수한 게임도")
    parser.add_argument("--dry-run", action="store_true", help="--import: 바꾸지 않고 결과만")
    parser.add_argument("--top", type=int, help="--export: 앞(홈에 많이 보일 게임)에서 N개만")
    parser.add_argument("--source", choices=("human", "ai"), default="human", help="--import: 태그를 누가 붙였나")
    args = parser.parse_args(argv)
    with get_connection(DB_PATH) as conn:
        if args.export:
            if args.top is not None and args.top < 1:
                parser.error("--top은 1 이상")
            print(f"{export(conn, include_checked=args.all, top=args.top)}개 → {CSV_PATH}")
        elif args.sources:
            print("| 출처 | 게임 | 그중 검수(맞춤 추천 우선) |")
            print("|---|---|---|")
            for name, total, checked in source_counts(conn):
                print(f"| {name} | {total} | {checked} |")
        elif args.tag_list:
            print(f"태그 {write_tag_list(conn)}개 → {TAG_LIST_PATH}")
        elif args.import_:
            report = import_csv(conn, dry_run=args.dry_run, source=args.source)
            print(f"태그 바꿈 {len(report['changed'])} · 검수 표시만 {len(report['checked_only'])} · 건너뜀 {len(report['skipped'])}"
                  + (" (dry-run: DB 안 바꿈)" if args.dry_run else ""))
            for game_id, reason in report["skipped"]:
                print(f"  건너뜀 {game_id}: {reason}")
        else:
            if len(args.add) < 2:
                parser.error("--add ID 태그 [태그 ...]")
            try:
                print(args.add[0], "→", "; ".join(add_tags(conn, args.add[0], args.add[1:])))
            except TagError as error:
                parser.exit(1, f"오류: {error}\n")


if __name__ == "__main__":
    main()

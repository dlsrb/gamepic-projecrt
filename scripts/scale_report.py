"""
규모 확장 실험 보고서 (C16). 실험 DB(data/big.db 등)와 시뮬 숫자만 읽고 docs/scale_report.md를 씀.
실제 DB(data/gamepick.db)·seed는 읽기 전용으로만 열고, 속도 측정은 임시 사본에서 함.

  python scripts/scale_report.py collect      # 수집 결과 → data/big_collect.json (수집 직후, 시뮬 전에)
  python scripts/scale_report.py bench        # 4가지 상태(C14·C16 × 시뮬 전·후) 속도·로딩 → data/big_bench.json
  python scripts/scale_report.py report       # 위 숫자 + 두 시뮬 숫자 → docs/scale_report.md
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA = BASE_DIR / "data"
REAL_DB = DATA / "gamepick.db"
BIG_DB, BIG_BASE = DATA / "big.db", DATA / "big_base.db"
C14_SIM_DB = DATA / "sim_c16base.db"                 # C14 조건(게임 280 · 사용자 300)을 지금 코드로 다시 돌린 것
C14_METRICS, C16_METRICS = DATA / "big_c14base_metrics.json", DATA / "big_metrics.json"
C14_ORIGINAL = DATA / "sim_metrics.json"             # C14 당시(C15 전 코드) 숫자, 참고용
COLLECT_JSON, BENCH_JSON = DATA / "big_collect.json", DATA / "big_bench.json"
REPORT = BASE_DIR / "docs" / "scale_report.md"
LOG_DIR = BASE_DIR / "logs"
BENCH_REPEAT = 30
GENRES = ("RPG", "액션", "어드벤처", "캐주얼", "퍼즐", "시뮬레이션", "전략")


def ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------- 수집 결과 ----------
def collect_stats() -> dict:
    real, big = ro(REAL_DB), ro(BIG_DB)
    try:
        old_ids = {r[0] for r in real.execute("SELECT id FROM games")}
        genre = {r[0]: r[1] for r in big.execute(
            "SELECT gt.game_id, t.name FROM game_tags gt JOIN tags t ON t.id = gt.tag_id WHERE t.category = 'genre'")}
        rows = big.execute("SELECT id, source_feed, review_status, android_id, ios_id, tag_source FROM games").fetchall()
        visible = [r for r in rows if r["review_status"] == "ok"]
        new = [r for r in visible if r["id"] not in old_ids]

        def store(r):
            return "둘 다" if r["android_id"] and r["ios_id"] else ("Android만" if r["android_id"] else "iOS만")

        def feed(r):
            f = r["source_feed"] or ""
            return "신작" if f.startswith("new") else ("직접 넣음" if f == "manual" else "차트")

        def table(items):
            return {
                "by_feed": dict(Counter(feed(r) for r in items)),
                "by_genre": {g: n for g, n in Counter(genre.get(r["id"], "?") for r in items).most_common()},
                "by_store": dict(Counter(store(r) for r in items)),
                "by_tag_source": dict(Counter(r["tag_source"] or "auto" for r in items)),
            }
        run = big.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
        stats = {
            "start_visible": sum(1 for r in real.execute("SELECT 1 FROM games WHERE review_status = 'ok'")),
            "end_visible": len(visible), "added_visible": len(new),
            "pending_total": sum(1 for r in rows if r["review_status"] == "pending"),
            "pending_new": sum(1 for r in rows if r["review_status"] == "pending" and r["id"] not in old_ids),
            "run": dict(run) if run else None,
            "added": table(new), "all": table(visible),
        }
        if run and run["finished_at"]:
            from datetime import datetime
            fmt = "%Y-%m-%d %H:%M:%S"
            stats["minutes"] = round((datetime.strptime(run["finished_at"], fmt) -
                                      datetime.strptime(run["started_at"], fmt)).total_seconds() / 60, 1)
        stats["chart_sizes"] = chart_sizes()
        stats["db_mb"] = round(BIG_DB.stat().st_size / 1e6, 2)
        return stats
    finally:
        real.close()
        big.close()


def chart_sizes() -> dict:
    """수집 로그에서 장르 차트마다 받은 개수 (가장 최근 실험 로그)"""
    logs = sorted(LOG_DIR.glob("sync_big_*.log"))
    if not logs:
        return {}
    sizes = {}
    for line in logs[-1].read_text(encoding="utf-8").splitlines():
        m = re.search(r"장르 차트 (\S+) (\S+) (free|grossing|paid) (\d+)개", line)
        if m:
            sizes[f"{m[1]} {m[2]} {m[3]}"] = int(m[4])
    return sizes


# ---------- 속도·로딩 (임시 사본, 상태마다 새 프로세스) ----------
BENCH_ANSWERS = {"q1": ["both"], "q2": ["any"], "q3": ["genre:RPG", "mood:판타지"], "q4": ["none"], "q5": []}


def bench_one(db: Path) -> dict:
    """이 프로세스 안에서: db(임시 사본)를 보는 앱으로 6개 API 속도 + Catalog 로딩·부분 시간·메모리"""
    os.environ.update(DB_PATH=str(db), RATE_LIMITS="off", STORE_SYNC="false", ICON_FETCH="false", BACKUP="false")
    sys.path.insert(0, str(BASE_DIR))
    from fastapi.testclient import TestClient

    from app import config, rankings, services
    from app.main import app
    assert Path(config.DB_PATH).resolve() == db.resolve()
    client = TestClient(app)
    client.post("/api/users")
    client.post("/api/survey/answers", json={"answers": BENCH_ANSWERS})
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    game_id = sorted(r[0] for r in conn.execute("SELECT id FROM games WHERE review_status = 'ok'"))[0]
    endpoints = {
        "홈 맞춤 추천": ("/api/feed", {"section": "match", "limit": 9, "min_match": 60}),
        "이달의 게임": ("/api/monthly", {}),
        "인기 랭킹": ("/api/popular", {"tab": "all"}),
        "추천 순위": ("/api/ranking", {}),
        "검색": ("/api/search", {"q": "RPG 판타지"}),
        "상세": (f"/api/games/{game_id}", {}),
    }
    for path, params in endpoints.values():   # 데우기
        client.get(path, params=params)
    speed = {}
    for name, (path, params) in endpoints.items():
        times = sorted(timed(lambda: client.get(path, params=params)) for _ in range(BENCH_REPEAT))
        speed[name] = {"median_ms": round(statistics.median(times), 1),
                       "p95_ms": round(times[int(len(times) * 0.95) - 1], 1)}
    user = conn.execute("SELECT * FROM users ORDER BY rowid DESC LIMIT 1").fetchone()
    catalog = services.Catalog(conn)
    parts = {
        "Catalog 로딩(게임·태그 전부)": lambda: services.Catalog(conn),
        "배지 계산(BadgeBook)": lambda: rankings.BadgeBook(services.Catalog(conn)) and None,
        "인기 순위 계산": lambda: rankings.popular(catalog),
        "맞춤 추천 계산(로딩 뺌)": lambda: services.match_feed(conn, user, catalog, 9, 60),
    }
    part_ms = {}
    for name, fn in parts.items():
        fn()
        part_ms[name] = round(statistics.median(timed(fn) for _ in range(15)), 1)
    part_ms["배지 계산(BadgeBook)"] = round(part_ms["배지 계산(BadgeBook)"] - part_ms["Catalog 로딩(게임·태그 전부)"], 1)
    tracemalloc.start()
    services.Catalog(conn)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    counts = {k: conn.execute(q).fetchone()[0] for k, q in {
        "visible_games": "SELECT COUNT(*) FROM games WHERE review_status = 'ok'",
        "game_tags": "SELECT COUNT(*) FROM game_tags",
        "users": "SELECT COUNT(*) FROM users", "reactions": "SELECT COUNT(*) FROM reactions",
        "snapshot_rows": "SELECT COUNT(*) FROM game_stats_daily"}.items()}
    conn.close()
    return {"speed": speed, "parts_ms": part_ms, "catalog_peak_kb": round(peak / 1024), "counts": counts,
            "catalog_games": len(catalog.games)}


def timed(fn) -> float:
    start = time.perf_counter()
    fn()
    return (time.perf_counter() - start) * 1000


def bench_all() -> dict:
    states = {"C14 시뮬 전": REAL_DB, "C14 시뮬 후": C14_SIM_DB, "C16 시뮬 전": BIG_BASE, "C16 시뮬 후": BIG_DB}
    result = {}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        for name, src in states.items():
            if not src.exists():
                print(f"{src} 없음 → {name} 건너뜀", file=sys.stderr)
                continue
            copy = Path(tmp) / f"{len(result)}.db"
            a, b = ro(src), sqlite3.connect(copy)
            try:
                a.backup(b)
            finally:
                a.close()
                b.close()
            env = {k: v for k, v in os.environ.items() if k not in ("DB_PATH", "RATE_LIMITS")}
            env["PYTHONIOENCODING"] = "utf-8"
            out = subprocess.run([sys.executable, __file__, "bench-one", str(copy)], env=env, capture_output=True,
                                 check=True, timeout=1800).stdout.decode("utf-8")
            result[name] = json.loads(out.strip().splitlines()[-1])
            result[name]["db_mb"] = round(src.stat().st_size / 1e6, 2)
            print(f"{name}: {result[name]['speed']}", file=sys.stderr)
    return result


# ---------- 보고서 ----------
def pct(x) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def write_report() -> None:
    c14 = json.loads(C14_METRICS.read_text(encoding="utf-8"))
    c16 = json.loads(C16_METRICS.read_text(encoding="utf-8"))
    col = json.loads(COLLECT_JSON.read_text(encoding="utf-8"))
    bench = json.loads(BENCH_JSON.read_text(encoding="utf-8"))
    orig = json.loads(C14_ORIGINAL.read_text(encoding="utf-8")) if C14_ORIGINAL.exists() else None
    notes = BASE_DIR / "docs" / "scale_notes.md"
    L = ["# 게임픽 규모 확장 실험 보고서", "",
         "> **규모 확장 실험(시뮬레이션), 실제 서비스·실제 피드백 아님.** 실험용 사본 `data/big.db`에서만 돌렸고, "
         "실제 DB·seed·배포 zip·GitHub에는 이 데이터가 없습니다. 가상 사용자의 행동은 `scripts/simulate_users.py`의 "
         "단순한 규칙(C14와 같은 10개 유형·같은 비율)이라, 숫자는 \"게임·사용자가 늘면 구조가 버티는가\"를 보는 용도입니다.", "",
         "| | C14 조건 | C16 조건 |", "|---|---|---|",
         f"| 화면에 보이는 게임 | {bench['C14 시뮬 전']['counts']['visible_games']} | {col['end_visible']} |",
         f"| 가상 사용자 · 방문 | {c14['args']['users']}명 · {c14['visits']}회 | {c16['args']['users']}명 · {c16['visits']}회 |",
         f"| 기간 · 시드 | {c14['args']['days']}일 · {c14['args']['seed']} | {c16['args']['days']}일 · {c16['args']['seed']} |",
         f"| 시뮬 걸린 시간 | {c14['elapsed_seconds'] / 60:.0f}분 | {c16['elapsed_seconds'] / 60:.0f}분 |", "",
         "C14 조건은 C14와 같은 명령(300명·시드 42·14일)을 **지금 코드(C15 반영)로 다시 돌린 것**입니다 "
         "(C14 당시 숫자는 C15 이전 코드라 직접 비교하지 않고 참고로만 적음).", ""]

    # 0. 수집
    a = col["added"]
    L += ["## 0. 게임 모으기 (C16-a)", "",
          f"- 280개에서 시작 → 화면 게임 **{col['end_visible']}개** (새로 {col['added_visible']}개, 목표 1,500), "
          f"걸린 시간 {col.get('minutes', '-')}분, 실패 {col['run']['failed'] if col['run'] else '-'}건",
          f"- 검수 대기(화면에 안 나옴): 새로 {col['pending_new']}개 (DB 전체 {col['pending_total']}개) — "
          "비슷한 이름의 게임이 있어 같은 게임인지 사람이 봐야 하는 것 (기존 규칙)",
          f"- 출처: 신작 {a['by_feed'].get('신작', 0)} · 차트 {a['by_feed'].get('차트', 0)} — 신작 경로(C7-f)에서 새 후보가 "
          "거의 없어(7-5) 목표 30% 몫을 차트가 채움",
          f"- 아이콘 안 받음 (실험 규칙) → 로컬 서버에서 새 게임 카드는 아이콘 없이 보임", "",
          "| 새로 넣은 게임 | 수 |", "|---|---|"]
    L += [f"| 출처 {k} | {v} |" for k, v in a["by_feed"].items()]
    L += [f"| 스토어 {k} | {v} |" for k, v in a["by_store"].items()]
    L += ["", "| 장르 | 새로 | 전체(화면) |", "|---|---|---|"]
    L += [f"| {g} | {a['by_genre'].get(g, 0)} | {col['all']['by_genre'].get(g, 0)} |"
          for g in sorted(col["all"]["by_genre"], key=lambda g: -col["all"]["by_genre"][g])]
    L += ["", "| 태그 출처 | 새로 | 전체(화면) |", "|---|---|---|"]
    L += [f"| {s} | {a['by_tag_source'].get(s, 0)} | {col['all']['by_tag_source'].get(s, 0)} |"
          for s in ("human", "ai", "store", "auto")]
    L += [""]

    # 1. 속도
    names = list(bench["C14 시뮬 전"]["speed"])
    L += ["## 1. 속도 (같은 컴퓨터에서 네 상태를 차례로, API마다 30번, 임시 사본)", "",
          "| API | C14 전 | C14 후 | C16 전 | C16 후 | C14→C16 (후 기준) |", "|---|---|---|---|---|---|"]
    growth = {}
    for n in names:
        cells = [bench[s]["speed"][n] for s in ("C14 시뮬 전", "C14 시뮬 후", "C16 시뮬 전", "C16 시뮬 후")]
        growth[n] = cells[3]["median_ms"] / cells[1]["median_ms"]
        L += [f"| {n} | " + " | ".join(f"{c['median_ms']} / {c['p95_ms']}" for c in cells) + f" | ×{growth[n]:.1f} |"]
    slowest = max(names, key=lambda n: bench["C16 시뮬 후"]["speed"][n]["median_ms"])
    most = max(growth, key=growth.get)
    L += ["", f"칸 = 중앙값 / 95% (ms). \"전\" = 실제 DB 사본 그대로(사용자 {bench['C14 시뮬 전']['counts']['users'] - 1}명), \"후\" = 시뮬 뒤. 측정용 사용자 1명을 더해 잼.", ""]
    if orig:
        o = orig["speed"]["after"]
        L += ["C14 당시(C15 전 코드) 시뮬 후 중앙값: " + ", ".join(f"{n} {o[n]['median_ms']}" for n in o) + " ms (참고)", ""]
    L += [f"- 가장 느린 API(C16 후): **{slowest}** {bench['C16 시뮬 후']['speed'][slowest]['median_ms']}ms · "
          f"가장 많이 느려진 API: **{most}** ×{growth[most]:.1f}", "",
          "요청 하나 안의 시간 나누기 (중앙값 ms):", "", "| 부분 | C14 후 | C16 후 |", "|---|---|---|"]
    for part in bench["C16 시뮬 후"]["parts_ms"]:
        L += [f"| {part} | {bench['C14 시뮬 후']['parts_ms'][part]} | {bench['C16 시뮬 후']['parts_ms'][part]} |"]
    L += [""]

    # 2. 비선호
    L += ["## 2. 비선호 위반 (0이어야 함)", "", "| | C14 | C16 |", "|---|---|---|",
          f"| 홈 맞춤 추천 | {c14['violations']['home']} / {c14['violations']['home_impressions']} | "
          f"{c16['violations']['home']} / {c16['violations']['home_impressions']} |",
          f"| 이달의 게임 | {c14['violations']['monthly']} / {c14['violations']['monthly_impressions']} | "
          f"{c16['violations']['monthly']} / {c16['violations']['monthly_impressions']} |",
          f"| 같은 게임 따봉 중복 기록 | {c14['duplicate_like_rows']} | {c16['duplicate_like_rows']} |", ""]

    # 3. 다양성
    d14, d16 = c14["diversity"], c16["diversity"]
    L += ["## 3. 다양성·쏠림 (홈 맞춤 추천)", "", "| | C14 | C16 |", "|---|---|---|",
          f"| 홈에 한 번이라도 나온 게임 / 화면 게임 | {d14['distinct_in_home']} / {d14['visible_games']} "
          f"({d14['distinct_in_home'] / d14['visible_games']:.0%}) | {d16['distinct_in_home']} / {d16['visible_games']} "
          f"({d16['distinct_in_home'] / d16['visible_games']:.0%}) |",
          f"| 상위 10개 게임 노출 비중 | {pct(d14['top10_share'])} | {pct(d16['top10_share'])} |"]
    for slot in ("추천", "탐색"):
        L += [f"| {slot} 칸: 노출 · 서로 다른 게임 | {d14['slots'][slot]['impressions']} · {d14['slots'][slot]['distinct']} | "
              f"{d16['slots'][slot]['impressions']} · {d16['slots'][slot]['distinct']} |"]
    L += ["", f"- C16 가장 많이 나온 10개: " + ", ".join(f"{n} {c}" for n, c in d16["top10"]), ""]

    # 4. 태그 출처
    L += ["## 4. 태그 출처별 노출·따봉률, 대체 동작, 미검수 채움", "",
          "| 태그 출처 | C14 노출 비중 | C14 따봉률 | C16 노출 비중 | C16 따봉률 |", "|---|---|---|---|---|"]
    for s in ("human", "ai", "store", "auto"):
        x, y = c14["tag_source"][s], c16["tag_source"][s]
        L += [f"| {s} | {pct(x['share'])} | {pct(x['like_rate'])} | {pct(y['share'])} | {pct(y['like_rate'])} |"]

    def unchecked(m):
        return sum(m["tag_source"][s]["share"] or 0 for s in ("store", "auto"))
    L += ["", "| | C14 | C16 |", "|---|---|---|",
          f"| 홈 대체 동작(60% 기준 미달) 방문 비율 | {pct(c14['home']['fallback_rate'])} | {pct(c16['home']['fallback_rate'])} |",
          f"| 미검수(store·auto) 게임이 홈 노출에서 차지한 비율 | {pct(unchecked(c14))} | {pct(unchecked(c16))} |",
          f"| 숨은 취향 일치 (전체) | {pct(avg_match(c14))} | {pct(avg_match(c16))} |", "",
          "유형별 숨은 취향 일치 · 따봉률:", "", "| 유형 | C14 | C16 |", "|---|---|---|"]
    for p in c16["personas"]:
        x, y = c14["home"]["by_persona"][p["key"]], c16["home"]["by_persona"][p["key"]]
        L += [f"| {p['label']} | {pct(x['hidden_match'])} · {pct(x['like_rate'])} | "
              f"{pct(y['hidden_match'])} · {pct(y['like_rate'])} |"]
    L += [""]

    # 5. 검색
    s14, s16 = c14["search"], c16["search"]
    L += ["## 5. 검색", "", "| | C14 | C16 |", "|---|---|---|",
          f"| 검색 수 | {s14['total']} | {s16['total']} |",
          f"| 결과 0개 | {s14['zero_count']} ({s14['zero_count'] / s14['total']:.1%}) | "
          f"{s16['zero_count']} ({s16['zero_count'] / s16['total']:.1%}) |",
          f"| 일부 일치(C15-b)로 보여 준 검색 | {s14['partial']} ({s14['partial'] / s14['total']:.1%}) | "
          f"{s16['partial']} ({s16['partial'] / s16['total']:.1%}) |",
          f"| 이름 검색으로 찾던 게임을 찾음 | {pct(s14['name_success'])} | {pct(s16['name_success'])} |", "",
          "C16 결과 0개 검색어: " + (", ".join(f"`{q}` × {n}" for q, n in s16["zero_result"][:15]) or "없음"), "",
          "C16 이름 검색 실패 예: " + (", ".join(f"`{q}` → {n}" for q, n in s16["name_failures"][:10]) or "없음"), ""]

    # 6. DB·메모리
    b14, b16 = bench["C14 시뮬 후"], bench["C16 시뮬 후"]
    L += ["## 6. DB 크기·메모리", "", "| | C14 후 | C16 후 |", "|---|---|---|",
          f"| DB 파일 | {b14['db_mb']} MB | {b16['db_mb']} MB |",
          f"| 게임(화면) · 게임-태그 줄 | {b14['counts']['visible_games']} · {b14['counts']['game_tags']} | "
          f"{b16['counts']['visible_games']} · {b16['counts']['game_tags']} |",
          f"| 사용자 · 반응 줄 | {b14['counts']['users']} · {b14['counts']['reactions']} | "
          f"{b16['counts']['users']} · {b16['counts']['reactions']} |",
          f"| 요청마다 Catalog 로딩 | {b14['parts_ms']['Catalog 로딩(게임·태그 전부)']} ms | "
          f"{b16['parts_ms']['Catalog 로딩(게임·태그 전부)']} ms |",
          f"| Catalog 한 번의 최대 메모리 | {b14['catalog_peak_kb']} KB | {b16['catalog_peak_kb']} KB |", "",
          "`Catalog`는 요청마다 화면 게임 전부와 태그 전부, games 테이블 전체를 새로 읽습니다 (캐시 없음). "
          "배지(BadgeBook)는 그 위에서 스냅샷·반응을 다시 셉니다.", ""]

    # 7. 문제·제안
    L += ["## 7. 발견한 문제와 제안 (고치지 않음)", ""]
    L += [notes.read_text(encoding="utf-8").strip() if notes.exists() else "(docs/scale_notes.md 없음)", ""]
    L += ["## 다시 만들기", "", "```powershell",
          "python scripts/sync_store.py --db data/big.db --target 1500 --genre-charts",
          "python scripts/scale_report.py collect",
          "python scripts/simulate_users.py --users 300 --seed 42 --days 14 --out data/sim_c16base.db "
          "--metrics data/big_c14base_metrics.json --report data/big_c14base_report.md --force",
          "python scripts/simulate_users.py --db data/big.db --users 1000 --seed 42 --days 14",
          "python scripts/scale_report.py bench",
          "python scripts/scale_report.py report", "```", ""]
    REPORT.write_text("\n".join(L), encoding="utf-8")


def avg_match(m) -> float | None:
    rows = m["home"]["by_group"].values()
    n = sum(r["impressions"] for r in rows)
    return sum(r["hidden_match"] * r["impressions"] for r in rows if r["hidden_match"] is not None) / n if n else None


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=["collect", "bench", "bench-one", "report"])
    parser.add_argument("db", nargs="?", type=Path)
    args = parser.parse_args(argv)
    if args.step == "collect":
        stats = collect_stats()
        COLLECT_JSON.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({k: stats[k] for k in ("start_visible", "end_visible", "added_visible", "pending_new")},
                         ensure_ascii=False), "→", COLLECT_JSON)
    elif args.step == "bench":
        BENCH_JSON.write_text(json.dumps(bench_all(), ensure_ascii=False, indent=1), encoding="utf-8")
        print("→", BENCH_JSON)
    elif args.step == "bench-one":
        print(json.dumps(bench_one(args.db), ensure_ascii=False))
    else:
        write_report()
        print("→", REPORT)


if __name__ == "__main__":
    main()

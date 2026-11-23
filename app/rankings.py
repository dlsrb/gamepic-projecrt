"""
인기 랭킹 · 자동 배지 · 이달의 게임 (PROJECT.md 4.11 C12-c·d·e). 숫자는 app/reco/reco_config.py.

- 인기 랭킹(popular): 스토어 무료 차트 순위. 전체 = 두 스토어 중 높은 순위(같으면 다른 쪽 순위), Google Play / App Store = 그 스토어 순위,
  장르 탭 = 전체 순서에서 그 장르 게임만. 차트 밖 게임은 없음
- 자동 배지(BadgeBook): 태그와 별개. 신작(출시 30일 이내) · 인기(전체 1~30위) · 급상승(스냅샷 비교) · 인디(설치 수 10만 이하)
- 급상승은 game_stats_daily(하루 1번 스냅샷, 스토어 자동 수집 끝에 저장) 비교라 기록이 7일 안 되면 "기록 쌓는 중"
- 이달의 게임(monthly): 이번 달 스냅샷의 평균 순위 + 급상승 점수 + 취향 % 약하게, 비선호(연관 태그 포함) 제외
"""
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone

from app.reco import reco_config as cfg
from app.reco.recommender import game_features, match_percent

ALL, PLAY, IOS = "all", "play", "ios"
BADGE_ORDER = ("신작", "인기", "급상승", "인디")
NO_RANK = 10_000


def tabs() -> list[dict]:
    return ([{"key": ALL, "label": "전체"}, {"key": PLAY, "label": "Google Play"}, {"key": IOS, "label": "App Store"}]
            + [{"key": g, "label": g} for g in cfg.RANKING_GENRE_TABS])


def best_rank(row) -> int | None:
    ranks = [r for r in (row["chart_rank_android"], row["chart_rank_ios"]) if r]
    return min(ranks) if ranks else None


def popular(catalog, tab: str = ALL, fill: bool = False) -> list[tuple[int | None, dict]]:
    """
    [(순위, 게임)] 순위는 1부터. tab: all | play | ios | 장르 이름 (장르 탭은 전체 순서 그대로 그 장르만).
    fill=True (랭킹 화면용, C12-k): 차트 순위 게임이 RANKING_MIN_ROWS개보다 적으면 차트 밖 게임을 이어서 채움 → 순위 None.
      차트 밖 후보: 장르 탭 = 그 장르 게임, Google Play 탭 = Play 링크 있는 게임, App Store 탭 = App Store 링크 있는 게임.
      순서는 offchart_order (설치 수·별점 수·따봉 수). 홈 인기 랭킹·"인기" 배지·이달의 게임은 fill 없이 차트 순위만
    """
    order = {g["id"]: i for i, g in enumerate(catalog.games)}

    def key(game):
        row = catalog.rows[game["id"]]
        if tab == PLAY:
            return (row["chart_rank_android"],) if row["chart_rank_android"] else None
        if tab == IOS:
            return (row["chart_rank_ios"],) if row["chart_rank_ios"] else None
        best = best_rank(row)
        if best is None:
            return None
        other = max(r or NO_RANK for r in (row["chart_rank_android"], row["chart_rank_ios"]))
        return (best, other)

    pool = catalog.games if tab in (ALL, PLAY, IOS) else [g for g in catalog.games if g["genre"] == tab]
    keyed = [(key(g), g) for g in pool]
    ranked = sorted(((k, g) for k, g in keyed if k), key=lambda x: (x[0], order[x[1]["id"]]))
    result: list[tuple[int | None, dict]] = [(i, g) for i, (_, g) in enumerate(ranked, 1)]
    if fill and len(result) < cfg.RANKING_MIN_ROWS:
        link = {PLAY: "android_url", IOS: "ios_url"}.get(tab)
        rest = [g for k, g in keyed if not k and (link is None or catalog.rows[g["id"]][link])]
        result += [(None, g) for g in offchart_order(catalog, rest)[:cfg.RANKING_MIN_ROWS - len(result)]]
    return result


def offchart_order(catalog, games: list[dict]) -> list[dict]:
    """차트 밖 게임 순서 (C12-k): Σ 무게 × log10(값+1) ÷ log10(최댓값+1), 값 = 설치 수·별점 수·게임픽 따봉 수"""
    import math

    likes = {r["game_id"]: r["n"] for r in catalog.conn.execute(
        "SELECT game_id, COUNT(*) AS n FROM reactions WHERE type = 'like' GROUP BY game_id")}
    values = {g["id"]: {"installs": catalog.rows[g["id"]]["installs"] or 0,
                        "ratings": catalog.rows[g["id"]]["store_rating_count"] or 0,
                        "likes": likes.get(g["id"], 0)} for g in games}
    top = {k: max((v[k] for v in values.values()), default=0) for k in cfg.OFFCHART_WEIGHTS}

    def scaled(value, k):
        return math.log10(value + 1) / math.log10(top[k] + 1) if top[k] > 0 else 0.0

    order = {g["id"]: i for i, g in enumerate(catalog.games)}
    score = {gid: sum(w * scaled(v[k], k) for k, w in cfg.OFFCHART_WEIGHTS.items()) for gid, v in values.items()}
    return sorted(games, key=lambda g: (-score[g["id"]], order[g["id"]]))


# ---------- 스냅샷 ----------
def save_daily_snapshot(conn: sqlite3.Connection, day: date) -> int:
    """오늘 숫자를 game_stats_daily에 (같은 날 다시 돌면 덮어씀). 스토어 자동 수집 끝에 부른다"""
    cur = conn.execute(
        "INSERT OR REPLACE INTO game_stats_daily (day, game_id, installs, rating_count, chart_rank_android, chart_rank_ios) "
        "SELECT ?, id, installs, store_rating_count, chart_rank_android, chart_rank_ios FROM games WHERE review_status = 'ok'",
        (day.isoformat(),))
    return cur.rowcount


def _normalize(text: str) -> str:
    return re.sub(r"[\W_]+", "", text or "").lower()


def _utc_cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


# ---------- 배지 ----------
class BadgeBook:
    """요청 하나 동안 쓰는 자동 배지 계산 (Catalog.badges로 한 번만 만듦)"""

    def __init__(self, catalog, today: date | None = None):
        self.catalog = catalog
        self.conn = catalog.conn
        self.today = today or date.today()
        self.surge, self.collecting = surge_scores(self.conn, catalog, self.today)
        popular_ids = [g["id"] for _, g in popular(catalog, ALL)[:cfg.POPULAR_BADGE_TOP]]
        top_surge = sorted((gid for gid, s in self.surge.items() if s >= cfg.SURGE_MIN_SCORE),
                           key=lambda gid: -self.surge[gid])[:cfg.SURGE_TOP]
        new_since = (self.today - timedelta(days=cfg.NEW_BADGE_DAYS)).isoformat()
        self.sets = {
            "신작": {gid for gid, row in catalog.rows.items()
                   if row["release_date"] and new_since <= row["release_date"] <= self.today.isoformat()},
            "인기": set(popular_ids),
            "급상승": set() if self.collecting else set(top_surge),
            "인디": {gid for gid, row in catalog.rows.items() if is_indie(row)},
        }

    def of(self, game_id: str) -> list[str]:
        return [b for b in BADGE_ORDER if game_id in self.sets[b]]

    def has(self, game_id: str, badge: str) -> bool:
        return game_id in self.sets.get(badge, set())


def is_indie(row) -> bool:
    """
    인디 배지: 설치 수를 알면 10만 이하, 모르면(iOS만) 기존 is_indie (개발사 목록 기준).
    단 직접 '인디 아님'으로 적어 둔 게임(is_indie = 0, 수작업 게임의 큰 회사 유료작 등 — 예: 드래곤 퀘스트 VIII)은 설치 수가 적어도 아님
    """
    if row["is_indie"] == 0:
        return False
    if row["installs"] is not None:
        return row["installs"] <= cfg.INDIE_MAX_INSTALLS
    return row["is_indie"] == 1


def surge_scores(conn: sqlite3.Connection, catalog, today: date) -> tuple[dict[str, float], bool]:
    """({game_id: 점수}, 기록 쌓는 중). 식은 reco_config의 SURGE_* 설명. 기록이 SURGE_MIN_DAYS일보다 짧으면 빈 점수 + True"""
    first = conn.execute("SELECT MIN(day) FROM game_stats_daily").fetchone()[0]
    if not first or first > (today - timedelta(days=cfg.SURGE_MIN_DAYS)).isoformat():
        return {}, True
    base_day = (today - timedelta(days=cfg.SURGE_WINDOW_DAYS)).isoformat()
    base = {row["game_id"]: row for row in conn.execute(
        """
        SELECT s.* FROM game_stats_daily s
        JOIN (SELECT game_id, MAX(day) AS day FROM game_stats_daily WHERE day <= ? GROUP BY game_id) last
          ON last.game_id = s.game_id AND last.day = s.day
        """, (base_day,))}
    cutoff = _utc_cutoff(cfg.SURGE_WINDOW_DAYS)
    keywords = [k for k in (_normalize(r["keyword"]) for r in conn.execute(
        "SELECT keyword FROM search_logs WHERE created_at >= ?", (cutoff,))) if len(k) >= 2]
    engage = {r["game_id"]: r["n"] for r in conn.execute(
        "SELECT game_id, COUNT(DISTINCT user_id) AS n FROM reactions WHERE type IN ('view', 'like') AND created_at >= ? "
        "GROUP BY game_id", (cutoff,))}
    w = cfg.SURGE_WEIGHTS
    scores = {}
    for game in catalog.games:
        gid = game["id"]
        if gid not in base:
            continue  # 비교할 7일 전 기록이 없는 게임 (새로 들어온 게임 등)
        now, then = catalog.rows[gid], base[gid]
        rating = 0.0
        if now["store_rating_count"] is not None and then["rating_count"] is not None:
            growth = now["store_rating_count"] - then["rating_count"]
            rating = min(1.0, max(0.0, growth / max(then["rating_count"], cfg.SURGE_RATING_FLOOR)))
        now_rank = best_rank(now)
        then_rank = best_rank({"chart_rank_android": then["chart_rank_android"], "chart_rank_ios": then["chart_rank_ios"]})
        rank = 0.0
        if now_rank and then_rank:
            rank = max(0, then_rank - now_rank) / 100
        elif now_rank:
            rank = (101 - now_rank) / 100  # 새로 차트에 들어옴
        installs = 1.0 if (now["installs"] and then["installs"] and now["installs"] > then["installs"]) else 0.0
        name = _normalize(game["name"])
        search = min(1.0, sum(1 for k in keywords if k in name or name in k) / cfg.SURGE_SEARCH_CAP)
        people = min(1.0, engage.get(gid, 0) / cfg.SURGE_ENGAGE_CAP)
        scores[gid] = round(w["rating"] * min(1.0, rating) + w["rank"] * min(1.0, rank) + w["installs"] * installs
                            + w["search"] * search + w["engage"] * people, 4)
    return scores, False


# ---------- 이달의 게임 ----------
def monthly(catalog, profile, today: date | None = None, size: int = cfg.MONTHLY_SIZE) -> list[dict]:
    """
    [{game, score, month_rank}] 점수 높은 순. 후보 = 이번 달 인기 상위 MONTHLY_POOL + 급상승 배지 게임.
    이번 달 인기 = 이번 달 스냅샷의 '두 스토어 중 높은 순위' 평균 (스냅샷이 없으면 지금 순위).
    점수 = popular 무게 × (1 − (이번 달 순위 − 1) ÷ POOL) + surge 무게 × 급상승 점수 + match 무게 × 취향 %/100.
    비선호(연관 태그 포함, profile.excluded)가 붙은 게임은 뺌. 설문 전이면 비선호 없음·취향 0.
    홈 바로 위 인기 랭킹 상위 MONTHLY_SKIP_POPULAR_TOP개도 뺌 (같은 게임이 두 번 보이지 않게)
    """
    today = today or date.today()
    conn, book = catalog.conn, catalog.badges
    month_start = today.replace(day=1).isoformat()
    averages = {r["game_id"]: r["avg"] for r in conn.execute(
        """
        SELECT game_id, AVG(MIN(COALESCE(chart_rank_android, ?), COALESCE(chart_rank_ios, ?))) AS avg
        FROM game_stats_daily WHERE day >= ? AND (chart_rank_android IS NOT NULL OR chart_rank_ios IS NOT NULL)
        GROUP BY game_id
        """, (NO_RANK, NO_RANK, month_start))}
    month_rank = {}
    for game in catalog.games:
        value = averages.get(game["id"]) or best_rank(catalog.rows[game["id"]])
        if value:
            month_rank[game["id"]] = value
    ordered = sorted(month_rank, key=lambda gid: month_rank[gid])
    position = {gid: i for i, gid in enumerate(ordered, 1)}
    shown_above = {g["id"] for _, g in popular(catalog, ALL)[:cfg.MONTHLY_SKIP_POPULAR_TOP]}  # 홈 인기 랭킹과 안 겹치게
    pool = (set(ordered[:cfg.MONTHLY_POOL]) | book.sets["급상승"]) - shown_above
    w = cfg.MONTHLY_WEIGHTS
    picked = []
    for gid in pool:
        game = catalog.get(gid)
        if game is None or set(game_features(game)) & profile.excluded:
            continue
        pos = position.get(gid)
        popular_part = max(0.0, 1 - (pos - 1) / cfg.MONTHLY_POOL) if pos else 0.0
        score = (w["popular"] * popular_part + w["surge"] * book.surge.get(gid, 0.0)
                 + w["match"] * match_percent(game, profile) / 100)
        picked.append({"game": game, "score": round(score, 4), "month_rank": pos})
    picked.sort(key=lambda x: (-x["score"], x["month_rank"] or NO_RANK))
    return picked[:size]

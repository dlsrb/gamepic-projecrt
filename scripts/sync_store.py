"""
스토어 자동 수집 (PROJECT.md 4.11 C7-a2). 하루 1번: 서버가 자동 실행(app/store_sync.py) 또는 직접 실행.

1. 차트 받기 (한국, 게임만, 각 100위)
   - App Store: 공개 RSS 피드 무료·매출·유료 (itunes.apple.com/kr/rss/...genre=6014). 신작 피드는 장르 필터가 안 돼서 안 씀
   - Google Play: 공식 차트 API가 없어 Play 웹의 내부 요청(batchexecute vyAe2) 사용 → 형식이 바뀌면 이 단계만 실패하고 계속
2. 이미 있는 게임: 숫자만 갱신 (설치 수·별점·별점 수·차트 순위, 비어 있으면 출시일), 규모 다시 분류(tier_locked면 그대로)
3. 새 게임: 차트 순위 높은 것부터, 한 번에 최대 NEW_GAME_LIMIT개
   - 같은 게임 판정: 스토어 ID가 같거나(App Store bundleId = Android 패키지 포함), 이름 유사도 0.6 이상 + 개발사가 같을 때만 합침.
     이름은 비슷한데 개발사가 다르면 합치지 않고 검수 대기(review_status=pending, 화면에 안 나옴)
   - 장르: data/store_genre_map.json, 나머지 태그: data/tag_keywords.json + 가격·인앱결제·광고 → game_tags.auto = 1
   - Google Play 태그 칩(app()의 categories 중 id 없는 것) → data/store_tag_map.json으로 우리 태그를 더함 (C12-j).
     대응되는 칩이 하나라도 있으면 games.tag_source = 'store' (아니면 'auto'). checked는 아님
   - 인디 여부: data/indie_developers.json에 있으면 1, 아니면 미정(NULL)
   - 아이콘: static/img/games/{id}.png
4. 최근 출시작 (C7-f): 차트와 상관없이 출시 NEW_RELEASE_DAYS일 이내·이미 출시된 게임을 설치 수·별점 수 기준 없이 넣음
   - App Store 신작 피드 3개(장르 필터가 안 돼서 lookup으로 게임만), Google Play는 data/new_release_queries.json 검색어
   - 출시 예정 제외: 출시일이 없거나 오늘 이후, Android 설치 수 0 / 상한 NEW_RELEASE_MAX_PER_RUN (차트 상한과 따로)
   - games.source_feed = 'new' (차트는 'chart') → 나중에 --prune-new로 기준 미달만 숨김
5. 스토어의 사용자 리뷰 글은 가져오지 않는다 (숫자만). 요청 사이 1초 이상, 실패한 게임은 건너뛰고 logs/sync_YYYYMMDD.log에 기록

실행: python scripts/sync_store.py                  수집 1번 (먼저 python scripts/init_db.py)
      python scripts/sync_store.py --limit 10       새 게임 최대 10개
      python scripts/sync_store.py --pending        검수 대기 목록
      python scripts/sync_store.py --approve ID ... 검수 대기 → 화면에 보이게
      python scripts/sync_store.py --reject ID ...  검수 대기 → 제외 (다음 수집 때 다시 안 들어옴)
      python scripts/sync_store.py --set-tier ID 중견  규모를 직접 정하고 자동 분류가 안 덮어쓰게
      python scripts/sync_store.py --new-limit 30   최근 출시작 최대 30개
      python scripts/sync_store.py --recheck-pending [--dry-run]  검수 대기 다시 판정 (C8-a)
      python scripts/sync_store.py --prune-new --min-installs 1000 --min-ratings 10 [--dry-run]
                                                     신작 경로 게임 중 기준 미달을 숨김 (지우지 않음)

규모 확장 실험 (C16, 실험 DB 전용 — 실제 서버의 매일 수집 범위는 그대로):
      python scripts/sync_store.py --db data/big.db --target 1500 --genre-charts
      → --db가 없으면 data/gamepick.db를 읽기 전용으로 열어 복사해서 만들고, 그 DB에만 씀. 실제 DB·seed는 --db로 못 줌
      → 신작(약 30%) 먼저, 나머지는 장르별 인기 차트(무료·매출·유료, 두 스토어)에서 장르를 돌아가며 상위부터 (한 장르 최대 절반)
      → 같은 게임 판정·검수 대기·태그·Play 태그 칩은 평소 규칙 그대로, 아이콘은 안 받음, 요청 간격 1초 그대로
"""
import argparse
import json
import logging
import re
import sys
import time
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from itertools import zip_longest
from difflib import SequenceMatcher
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import requests  # noqa: E402
from google_play_scraper import app as play_app  # noqa: E402
from google_play_scraper.exceptions import NotFoundError  # noqa: E402

from app.config import DB_PATH, STATIC_DIR  # noqa: E402
from app.db import get_connection  # noqa: E402
from app.rankings import save_daily_snapshot  # noqa: E402
from scripts.fetch_game_meta import APPSTORE_URL, PLAY_URL, similarity  # noqa: E402

DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
ICON_DIR = STATIC_DIR / "img" / "games"

REQUEST_GAP_SECONDS = 1.0
TIMEOUT_SECONDS = 20
CHART_SIZE = 100
NEW_GAME_LIMIT = 50          # 한 번에 새로 추가하는 게임 수 상한
CANDIDATE_LIMIT = 150        # 한 번에 살펴보는 새 게임 후보 수 상한 (요청 수 제한)
NAME_MATCH = 0.6             # 같은 게임으로 볼 이름 유사도 (이전 Gemini 규칙)
DEVELOPER_MATCH = 0.8
RUNNING_STALE_HOURS = 3      # 이보다 오래된 'running' 기록은 멈춘 것으로 보고 새로 실행
USER_AGENT = "Mozilla/5.0 (compatible; GamePick-sync/1.0)"

IOS_CHARTS = {"free": "topfreeapplications", "grossing": "topgrossingapplications", "paid": "toppaidapplications"}
# 최근 출시작 (C7-f): 신작 피드는 장르 필터가 안 먹어서 lookup으로 게임만 남긴다
IOS_NEW_FEEDS = {"new": "newapplications", "new_free": "newfreeapplications", "new_paid": "newpaidapplications"}
IOS_GAME_GENRE = 6014
NEW_RELEASE_DAYS = 90             # "최근" = 출시 90일 이내 (홈 신작 탭과 같은 기준)
NEW_RELEASE_MAX_PER_RUN = 100     # 최근 출시작 상한 (차트용 NEW_GAME_LIMIT와 따로)
NEW_QUERY_RESULTS = 20            # Play 신작 검색어 하나당 살펴볼 결과 수
IOS_CHART_URL = "https://itunes.apple.com/kr/rss/{feed}/limit={n}/genre=6014/json"
IOS_GENRE_CHART_URL = "https://itunes.apple.com/kr/rss/{feed}/limit={n}/genre={genre}/json"   # C16 실험 전용
IOS_LOOKUP_URL = "https://itunes.apple.com/lookup"
IOS_SEARCH_URL = "https://itunes.apple.com/search"
IOS_LOOKUP_BATCH = 100
ANDROID_CHARTS = {"free": "topselling_free", "grossing": "topgrossing", "paid": "topselling_paid"}
PLAY_BATCH_URL = ("https://play.google.com/_/PlayStoreUi/data/batchexecute?rpcids=vyAe2&source-path=%2Fstore%2Fapps"
                  "&hl=ko&gl=KR&authuser=0&soc-app=121&soc-platform=1&soc-device=1&rt=c")
PLAY_SEARCH_URL = "https://play.google.com/store/search"
PACKAGE_IN_CHART = re.compile(r'\\"([a-zA-Z][\w]*(?:\.[\w]+)+)\\",7\]')
PACKAGE_IN_PAGE = re.compile(r"/store/apps/details\?id=([\w.]+)")
# ---------- 규모 확장 실험 (C16, --db 실험 DB에서만) ----------
# 우리 장르 → 그 장르의 스토어 분류 차트 [(스토어, App Store 장르 ID 또는 Play 카테고리)]. 분류는 store_genre_map.json과 같은 대응
GENRE_CHARTS = {
    "RPG": [("ios", 7014), ("android", "GAME_ROLE_PLAYING")],
    "액션": [("ios", 7001), ("ios", 7013), ("android", "GAME_ACTION"), ("android", "GAME_RACING")],
    "어드벤처": [("ios", 7002), ("android", "GAME_ADVENTURE")],
    "캐주얼": [("ios", 7003), ("ios", 7016), ("android", "GAME_CASUAL"), ("android", "GAME_ARCADE"),
             ("android", "GAME_SPORTS")],
    "퍼즐": [("ios", 7012), ("ios", 7004), ("android", "GAME_PUZZLE"), ("android", "GAME_BOARD"), ("android", "GAME_WORD")],
    "시뮬레이션": [("ios", 7015), ("android", "GAME_SIMULATION")],
    "전략": [("ios", 7017), ("ios", 7005), ("android", "GAME_STRATEGY"), ("android", "GAME_CARD")],
}
GENRE_CHART_KINDS = ("free", "grossing", "paid")
GENRE_CHART_SIZE = 200             # Play는 200까지, App Store RSS는 100까지만 옴
GENRE_SHARE_MAX = 0.5              # 차트 몫에서 한 장르(차트 묶음)가 넘지 않을 비율
CHART_SHARE = 0.7                  # 목표 중 차트 몫 (나머지 신작). 신작이 모자라면 남는 몫은 차트가 채움

DEVELOPER_SUFFIXES = re.compile(r"(주식회사|\(주\)|corporation|corp|company|co|ltd|limited|inc|llc|pte|gmbh|games?|"
                                r"studios?|entertainment|interactive)$")

log = logging.getLogger("sync_store")


# ---------- 데이터 파일 ----------
def load_json(name: str) -> dict:
    data = json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


# ---------- 스토어 요청 ----------
class StoreClient:
    """스토어 요청을 모두 여기로 (요청 사이 간격 지키기). 테스트는 같은 메서드를 가진 가짜로 바꾼다."""

    def __init__(self, gap: float = REQUEST_GAP_SECONDS):
        self.gap = gap
        self.last = 0.0
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.chart_request = load_json("play_chart_request.json")["request"]

    def _wait(self):
        delay = self.last + self.gap - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.last = time.monotonic()

    def _get(self, url, **kwargs) -> requests.Response:
        self._wait()
        response = self.session.get(url, timeout=TIMEOUT_SECONDS, **kwargs)
        response.raise_for_status()
        return response

    def _ios_feed(self, feed: str) -> list[dict]:
        entries = self._get(IOS_CHART_URL.format(feed=feed, n=CHART_SIZE)).json()["feed"].get("entry", [])
        if isinstance(entries, dict):
            entries = [entries]
        return [{"ios_id": e["id"]["attributes"]["im:id"], "name": e["im:name"]["label"]} for e in entries]

    def ios_chart(self, kind: str) -> list[dict]:
        """[{ios_id, name}] 순위 순"""
        return self._ios_feed(IOS_CHARTS[kind])

    def ios_genre_chart(self, kind: str, genre: int) -> list[dict]:
        """장르별 차트 [{ios_id, name}] (C16 실험 전용)"""
        entries = self._get(IOS_GENRE_CHART_URL.format(feed=IOS_CHARTS[kind], n=GENRE_CHART_SIZE, genre=genre)).json()
        entries = entries["feed"].get("entry", [])
        if isinstance(entries, dict):
            entries = [entries]
        return [{"ios_id": e["id"]["attributes"]["im:id"], "name": e["im:name"]["label"]} for e in entries]

    def ios_new(self, kind: str) -> list[dict]:
        """신작 피드 [{ios_id, name}] — 게임이 아닌 앱도 섞여 있음 (lookup 뒤 거름)"""
        return self._ios_feed(IOS_NEW_FEEDS[kind])

    def ios_lookup(self, ids: list[str]) -> dict[str, dict]:
        found = {}
        for i in range(0, len(ids), IOS_LOOKUP_BATCH):
            chunk = ids[i:i + IOS_LOOKUP_BATCH]
            results = self._get(IOS_LOOKUP_URL, params={"id": ",".join(chunk), "country": "kr"}).json()["results"]
            found.update({str(r["trackId"]): r for r in results if r.get("trackId")})
        return found

    def ios_bundle(self, bundle_id: str) -> list[dict]:
        """bundleId로 App Store 앱 찾기 (Android 패키지 이름과 같은 게임이 있음)"""
        return self._get(IOS_LOOKUP_URL, params={"bundleId": bundle_id, "country": "kr"}).json()["results"]

    def ios_search(self, term: str, n: int = 10) -> list[dict]:
        return self._get(IOS_SEARCH_URL, params={"term": term, "country": "kr", "entity": "software",
                                                 "limit": n}).json()["results"]

    def android_chart(self, kind: str, category: str = "GAME", size: int = CHART_SIZE) -> list[str]:
        """패키지 이름 목록, 순위 순. category·size는 C16 실험(장르별 차트)에서만 바꿈"""
        request = json.loads(json.dumps(self.chart_request))
        request[0][1][0][1][1] = size                                        # [[8, [20, 개수]], ...]
        request[0][2][1], request[0][2][2] = ANDROID_CHARTS[kind], category  # [2, 차트, 카테고리]
        body = {"f.req": json.dumps([[["vyAe2", json.dumps(request, separators=(",", ":")), None, "generic"]]])}
        self._wait()
        response = self.session.post(PLAY_BATCH_URL, data=body, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        packages = list(dict.fromkeys(PACKAGE_IN_CHART.findall(response.text)))
        if not packages:
            raise ValueError("차트 응답에서 게임을 찾지 못함 (요청 형식이 바뀌었을 수 있음)")
        return packages

    def play_details(self, package: str) -> dict | None:
        self._wait()
        try:
            return play_app(package, lang="ko", country="kr")
        except NotFoundError:
            return None

    def play_search(self, term: str, n: int = 3) -> list[str]:
        # google-play-scraper의 search()는 이름이 정확히 같은 첫 결과의 ID를 비워서 돌려줌 → 검색 페이지에서 직접 뽑음
        page = self._get(PLAY_SEARCH_URL, params={"q": term, "c": "apps", "hl": "ko", "gl": "KR"}).text
        return list(dict.fromkeys(PACKAGE_IN_PAGE.findall(page)))[:n]

    def image(self, url: str) -> bytes:
        return self._get(url).content


# ---------- 스토어 응답 → 한 모양 ----------
def parse_date(text) -> str | None:
    """'2026-10-28T07:00:00Z', '2026. 10. 27.' → '2026-10-28'"""
    match = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", str(text or ""))
    if not match:
        return None
    try:
        return date(*map(int, match.groups())).isoformat()
    except ValueError:
        return None


def from_play(package: str, info: dict) -> dict:
    return {
        "android_id": package, "ios_id": None,
        "name": (info.get("title") or "").strip(), "developer": info.get("developer"),
        "description": info.get("summary") or None,
        "icon_url": f"{info['icon']}=s256" if info.get("icon") else None,
        "release_date": parse_date(info.get("released")),
        "installs": info.get("minInstalls"),
        "rating": round(info["score"], 2) if info.get("score") else None,
        "rating_count": info.get("ratings") or None,
        "store_genres": {"android": info.get("genreId")},
        "paid": bool(info.get("price")) and not info.get("free", True),
        "iap": bool(info.get("offersIAP")), "ads": bool(info.get("containsAds")),
        "text": " ".join(str(info.get(k) or "") for k in ("title", "summary", "description")),
        "is_game": str(info.get("genreId") or "").startswith("GAME"),
        "store_chips": play_chips(info),
    }


def play_chips(info: dict) -> list[str]:
    """Play 페이지 태그 칩 이름 ("싱글 플레이어" 등). categories 중 id가 있는 것은 Play 장르라 뺌 (C12-j)"""
    return list(dict.fromkeys(c["name"] for c in info.get("categories") or [] if not c.get("id") and c.get("name")))


def chip_tags(chips: list[str], chip_map: dict) -> list[str]:
    """태그 칩 → 우리 태그 '종류:이름' 목록 (중복 없이, 대응표에 없는 칩은 버림)"""
    return list(dict.fromkeys(feature for chip in chips for feature in chip_map.get(chip, [])))


def from_ios(info: dict) -> dict:
    description = (info.get("description") or "").strip()
    first_line = description.splitlines()[0][:100] if description else None
    return {
        "android_id": None, "ios_id": str(info["trackId"]), "bundle_id": info.get("bundleId"),
        "name": (info.get("trackName") or "").strip(), "developer": info.get("artistName") or info.get("sellerName"),
        "description": first_line,
        "icon_url": re.sub(r"\d+x\d+bb", "256x256bb", info["artworkUrl512"]) if info.get("artworkUrl512") else None,
        "release_date": parse_date(info.get("releaseDate")),
        "installs": None,
        "rating": round(info["averageUserRating"], 2) if info.get("averageUserRating") else None,
        "rating_count": info.get("userRatingCount") or None,
        "store_genres": {"ios": [g for g in info.get("genres", []) if g != "게임"]},
        "paid": bool(info.get("price")), "iap": False, "ads": False,
        "text": " ".join(str(info.get(k) or "") for k in ("trackName", "description")),
        "is_game": info.get("primaryGenreId") == IOS_GAME_GENRE
        or str(IOS_GAME_GENRE) in [str(g) for g in info.get("genreIds", [])],
        "store_chips": [],  # App Store는 태그 칩이 없음 (장르 목록뿐)
    }


def combine(play: dict, ios: dict) -> dict:
    """같은 게임의 Android·iOS 정보 합치기. 숫자·설치 수·과금 정보는 Android, 이름은 App Store(대체로 짧음)"""
    merged = dict(play)
    merged.update(ios_id=ios["ios_id"], name=ios["name"] or play["name"],
                  description=play["description"] or ios["description"],
                  icon_url=play["icon_url"] or ios["icon_url"],
                  release_date=min(d for d in (play["release_date"], ios["release_date"], "9999-12-31") if d),
                  store_genres={**play["store_genres"], **ios["store_genres"]},
                  paid=play["paid"] or ios["paid"], text=f"{play['text']} {ios['text']}")
    if merged["release_date"] == "9999-12-31":
        merged["release_date"] = None
    return merged


# ---------- 같은 게임 판정 ----------
def name_variants(name: str) -> set[str]:
    """이름 그대로 + 괄호 밖 + 괄호 안 (이전 Gemini 규칙). 콜론 앞부분은 쓰지 않음 (쿠키런: 킹덤 ≠ 쿠키런: 오븐브레이크)"""
    variants = {name}
    outside = re.sub(r"[(\[（].*?[)\]）]", " ", name).strip()
    variants.add(outside)
    variants.update(re.findall(r"[(\[（](.*?)[)\]）]", name))
    return {v for v in variants if len(re.sub(r"\W", "", v)) >= 2}


def name_similarity(a: str, b: str) -> float:
    return max((similarity(x, y) for x in name_variants(a) for y in name_variants(b)), default=0.0)


def normalize_developer(name: str | None) -> str:
    text = re.sub(r"[\W_]+", " ", (name or "").lower()).strip()
    words = text.split()
    while len(words) > 1 and DEVELOPER_SUFFIXES.fullmatch(words[-1]):
        words.pop()
    return "".join(words)


def same_developer(a: str | None, b: str | None) -> bool:
    x, y = normalize_developer(a), normalize_developer(b)
    if not x or not y:
        return False
    if x == y or (min(len(x), len(y)) >= 4 and (x in y or y in x)):
        return True
    return SequenceMatcher(None, x, y).ratio() >= DEVELOPER_MATCH


# ---------- 분류·태그 ----------
def classify_tier(installs: int | None, rating_count: int | None) -> str | None:
    """규모: Android 설치 수 1,000만 이상 대형 / 100만 이상 중견 / 그 아래 소형.
    설치 수가 없으면(iOS만) 별점 수로 비슷하게: 5만 이상 대형 / 5천 이상 중견 / 그 아래 소형"""
    if installs is not None:
        return "대형" if installs >= 10_000_000 else "중견" if installs >= 1_000_000 else "소형"
    if rating_count is not None:
        return "대형" if rating_count >= 50_000 else "중견" if rating_count >= 5_000 else "소형"
    return None


def store_genre(app: dict, genre_map: dict) -> tuple[str | None, str]:
    """(우리 장르 또는 None, 스토어 장르 설명)"""
    android = app["store_genres"].get("android")
    if android and android in genre_map["android"]:
        return genre_map["android"][android], android
    for name in app["store_genres"].get("ios", []):
        if name in genre_map["ios"]:
            return genre_map["ios"][name], name
    return None, android or ",".join(app["store_genres"].get("ios", [])) or "?"


def keyword_tags(text: str, keywords: dict) -> list[str]:
    found = []
    for feature, words in keywords.items():
        for word in words:
            if re.fullmatch(r"[A-Za-z0-9 \-]+", word):
                hit = re.search(rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])", text, re.IGNORECASE)
            else:
                hit = word in text
            if hit:
                found.append(feature)
                break
    return found


def bm_tags(app: dict) -> list[str]:
    tags = ["bm:유료구매"] if app["paid"] else (["bm:무료+과금"] if app["iap"] else [])
    if app["ads"]:
        tags.append("bm:광고")
    return tags


# ---------- 수집 ----------
def is_prerelease(app: dict, today: date) -> bool:
    """출시 예정(사전 주문·사전 등록): 출시일이 없거나 오늘 이후, Android는 설치 수 0도 (C7-f)"""
    if not app.get("release_date") or app["release_date"] > today.isoformat():
        return True
    return bool(app.get("android_id")) and not app.get("installs")


class Sync:
    def __init__(self, conn, client, limit: int, today: date, new_limit: int = NEW_RELEASE_MAX_PER_RUN,
                 save_icons: bool = True):
        self.conn, self.client, self.limit, self.today = conn, client, limit, today
        self.new_limit = new_limit
        self.save_icons = save_icons  # C16 실험은 아이콘을 안 받음
        self.since = (today - timedelta(days=NEW_RELEASE_DAYS)).isoformat()
        self.genre_map = load_json("store_genre_map.json")
        self.keywords = load_json("tag_keywords.json")
        self.chip_map = load_json("store_tag_map.json")  # C12-j
        self.indie = {normalize_developer(d) for d in load_json("indie_developers.json")["developers"]}
        self.report = {"added": 0, "updated": 0, "pending": 0, "failed": 0, "skipped": 0, "notes": [],
                       # 최근 출시작 (C7-f)
                       "new_added": 0, "new_pending": 0, "new_ios_only": 0, "new_android_only": 0, "new_both": 0,
                       "new_prerelease": 0, "new_too_old": 0, "new_not_game": 0, "new_capped": 0,
                       "new_from_ios_feed": 0, "new_from_play_search": 0, "existing_prerelease": []}
        self.ios_ranks: dict[str, int] = {}       # 무료 차트 순위
        self.android_ranks: dict[str, int] = {}
        self.charts_ok = {"ios": False, "android": False}

    def now(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def note(self, message: str):
        log.warning(message)
        self.report["notes"].append(message)

    # --- 1. 차트 ---
    def fetch_charts(self) -> tuple[dict[str, int], dict[str, int]]:
        """(App Store ID → 가장 좋은 순위, 패키지 → 가장 좋은 순위). 무료 차트 순위는 따로 저장 (홈 인기 탭)"""
        ios_best, android_best = {}, {}
        for kind in IOS_CHARTS:
            try:
                entries = self.client.ios_chart(kind)
            except Exception as error:  # noqa: BLE001 - 차트 하나 실패해도 계속
                self.note(f"App Store {kind} 차트 실패: {error}")
                continue
            log.info("App Store %s 차트 %d개", kind, len(entries))
            for rank, entry in enumerate(entries, 1):
                ios_best[entry["ios_id"]] = min(rank, ios_best.get(entry["ios_id"], rank))
                if kind == "free":
                    self.ios_ranks[entry["ios_id"]] = rank
            self.charts_ok["ios"] |= kind == "free"
        for kind in ANDROID_CHARTS:
            try:
                packages = self.client.android_chart(kind)
            except Exception as error:  # noqa: BLE001 - 비공식 요청이라 실패해도 전체는 계속
                self.note(f"Google Play {kind} 차트 실패: {error}")
                continue
            log.info("Google Play %s 차트 %d개", kind, len(packages))
            for rank, package in enumerate(packages, 1):
                android_best[package] = min(rank, android_best.get(package, rank))
                if kind == "free":
                    self.android_ranks[package] = rank
            self.charts_ok["android"] |= kind == "free"
        return ios_best, android_best

    # --- DB 도우미 ---
    def games(self) -> list:
        return self.conn.execute("SELECT * FROM games").fetchall()

    def find(self, app: dict) -> tuple[object, str]:
        """(DB 행, 'id' | 'name' | 'ambiguous') 또는 (None, '')"""
        rows = self.games()
        ids = {app.get("android_id"), app.get("bundle_id")} - {None}
        for row in rows:
            if (row["android_id"] and row["android_id"] in ids) or (app.get("ios_id") and row["ios_id"] == app["ios_id"]):
                return row, "id"
        similar = [row for row in rows if name_similarity(row["name"], app["name"]) >= NAME_MATCH]
        for row in similar:
            if same_developer(row["developer"], app["developer"]):
                return row, "name"
        return (similar[0], "ambiguous") if similar else (None, "")

    def save_icon(self, game_id: str, url: str | None) -> str | None:
        if not url or not self.save_icons:
            return None
        try:
            content = self.client.image(url)
            if not content.startswith((b"\x89PNG", b"\xff\xd8\xff", b"RIFF")):
                raise ValueError("이미지가 아닌 응답")
            ICON_DIR.mkdir(parents=True, exist_ok=True)
            (ICON_DIR / f"{game_id}.png").write_bytes(content)
            return f"/static/img/games/{game_id}.png"
        except Exception as error:  # noqa: BLE001 - 아이콘이 없어도 게임은 추가 (화면은 대체 표시)
            self.note(f"{game_id} 아이콘 실패: {error}")
            return None

    def new_id(self, app: dict) -> str:
        if app.get("android_id"):
            base = re.sub(r"^(com|net|jp|kr|io|co)\.", "", app["android_id"]).replace(".", "-")
        else:
            base = f"ios-{app['ios_id']}"
        base = re.sub(r"[^A-Za-z0-9_-]", "", base)[:36] or "game"
        game_id, n = base, 2
        while self.conn.execute("SELECT 1 FROM games WHERE id = ?", (game_id,)).fetchone():
            game_id, n = f"{base[:36]}-{n}", n + 1
        return game_id

    def add_tag(self, game_id: str, feature: str):
        category, name = feature.split(":", 1)
        self.conn.execute(
            "INSERT OR IGNORE INTO game_tags (game_id, tag_id, auto) SELECT ?, id, 1 FROM tags WHERE category = ? AND name = ?",
            (game_id, category, name))

    # --- 2. 이미 있는 게임: 숫자만 ---
    def update_numbers(self, row, app: dict, ios: dict | None = None):
        installs = app["installs"] if app.get("android_id") else row["installs"]
        rating_count = app["rating_count"]
        tier = row["tier"] if row["tier_locked"] else (classify_tier(installs, rating_count) or row["tier"])
        self.conn.execute(
            """
            UPDATE games SET installs = ?, store_rating = ?, store_rating_count = ?, tier = ?,
                   release_date = COALESCE(release_date, ?), developer = COALESCE(developer, ?),
                   android_id = COALESCE(android_id, ?), android_url = COALESCE(android_url, ?),
                   ios_id = COALESCE(ios_id, ?), ios_url = COALESCE(ios_url, ?), updated_at = ?
            WHERE id = ?
            """,
            (installs, app["rating"] or row["store_rating"], rating_count or row["store_rating_count"], tier,
             app["release_date"], app["developer"],
             app.get("android_id"), PLAY_URL.format(app["android_id"]) if app.get("android_id") else None,
             app.get("ios_id"), APPSTORE_URL.format(app["ios_id"]) if app.get("ios_id") else None,
             self.now(), row["id"]))
        self.report["updated"] += 1

    def refresh_existing(self, ios_info: dict[str, dict]):
        for row in self.conn.execute("SELECT * FROM games WHERE review_status = 'ok'").fetchall():
            try:
                if row["android_id"]:
                    info = self.client.play_details(row["android_id"])
                    if info is None:
                        raise LookupError(f"Google Play에 없음 ({row['android_id']})")
                    self.update_numbers(row, from_play(row["android_id"], info))
                elif row["ios_id"] and row["ios_id"] in ios_info:
                    self.update_numbers(row, from_ios(ios_info[row["ios_id"]]))
                else:
                    continue
                self.conn.commit()
            except Exception as error:  # noqa: BLE001 - 게임 하나 실패해도 계속
                self.conn.rollback()
                self.report["failed"] += 1
                self.note(f"갱신 실패 {row['id']} {row['name']}: {error}")

    def save_chart_ranks(self):
        for store, ranks in (("android", self.android_ranks), ("ios", self.ios_ranks)):
            if not self.charts_ok[store]:  # 차트를 못 받았으면 어제 순위를 그대로 둔다
                continue
            self.conn.execute(f"UPDATE games SET chart_rank_{store} = NULL")
            for store_id, rank in ranks.items():
                self.conn.execute(f"UPDATE games SET chart_rank_{store} = ? WHERE {store}_id = ?", (rank, store_id))
        self.conn.commit()

    # --- 3. 새 게임 ---
    def pair_android(self, ios_app: dict, android_chart: set[str]) -> tuple[dict | None, str]:
        """iOS 게임의 Android 짝 (정보, 'id' | 'name') 또는 (None, '') — 애매하면 짝 없음 (C8-a)"""
        bundle = ios_app.get("bundle_id")
        if bundle and re.fullmatch(r"[A-Za-z][\w]*(\.[\w]+)+", bundle):
            info = self.client.play_details(bundle)
            if info is not None:
                play = from_play(bundle, info)
                # Play 쪽이 아직 사전 등록이면 짝을 붙이지 않음 (iOS만으로 넣고, 출시되면 다음 수집 때 합쳐짐)
                return (None, "") if is_prerelease(play, self.today) else (play, "id")
        for package in self.client.play_search(ios_app["name"])[:1]:
            info = self.client.play_details(package)
            if info is None:
                continue
            play = from_play(package, info)
            if is_prerelease(play, self.today):
                continue
            if name_similarity(play["name"], ios_app["name"]) >= NAME_MATCH:
                if same_developer(play["developer"], ios_app["developer"]):
                    return play, "name"
                # C8-a: 이름 검색으로만 찾은 짝이 개발사가 다르면 검수 대기로 보내지 않고 iOS만으로 넣는다
                # (검수 대기는 DB에 이미 있는 게임과 이름이 비슷할 때만 — find()에서)
                log.info("Android 짝 아님 (개발사 다름): %s (%s) ↔ %s %s (%s)", ios_app["name"], ios_app["developer"],
                         package, play["name"], play["developer"])
        return None, ""

    def insert_game(self, app: dict, status: str, similar_to: str | None = None, feed: str = "chart") -> str | None:
        genre, raw_genre = store_genre(app, self.genre_map)
        if genre is None:
            self.report["skipped"] += 1
            log.info("건너뜀 (장르 %s는 추가 대상 아님): %s", raw_genre, app["name"])
            return None
        game_id = self.new_id(app)
        is_indie = 1 if normalize_developer(app["developer"]) in self.indie else None
        self.conn.execute(
            """
            INSERT INTO games (id, name, developer, tier, is_indie, description, thumbnail_url, android_id, ios_id,
                               android_url, ios_url, release_date, store_rating, store_rating_count, installs,
                               chart_rank_android, chart_rank_ios, source, source_feed, review_status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'auto', ?, ?, ?)
            """,
            (game_id, app["name"], app["developer"], classify_tier(app["installs"], app["rating_count"]), is_indie,
             app["description"], app.get("android_id"), app.get("ios_id"),
             PLAY_URL.format(app["android_id"]) if app.get("android_id") else None,
             APPSTORE_URL.format(app["ios_id"]) if app.get("ios_id") else None,
             app["release_date"], app["rating"], app["rating_count"], app["installs"],
             self.android_ranks.get(app.get("android_id")), self.ios_ranks.get(app.get("ios_id")),
             feed, status, self.now()))
        self.add_tag(game_id, f"genre:{genre}")
        for feature in keyword_tags(app["text"], self.keywords) + bm_tags(app):
            self.add_tag(game_id, feature)
        store_features = chip_tags(app.get("store_chips", []), self.chip_map)  # C12-j: Play 태그 칩
        for feature in store_features:
            self.add_tag(game_id, feature)
        if store_features:
            self.conn.execute("UPDATE games SET tag_source = 'store' WHERE id = ?", (game_id,))
        icon = self.save_icon(game_id, app["icon_url"])
        self.conn.execute("UPDATE games SET thumbnail_url = ? WHERE id = ?", (icon, game_id))
        if status == "pending":  # 개수는 아래에서 경로(차트·신작)별로 셈
            self.note(f"검수 대기 {game_id} {app['name']} ({app['developer']}) — 비슷한 게임: {similar_to}")
        else:
            log.info("추가%s %s %s (%s, %s)", " [신작]" if feed == "new" else "", game_id, app["name"],
                     app["developer"], genre)
        if feed == "new":
            self.report["new_pending" if status == "pending" else "new_added"] += 1
            kind = "new_both" if app.get("android_id") and app.get("ios_id") else \
                "new_android_only" if app.get("android_id") else "new_ios_only"
            self.report[kind] += 1
        else:
            self.report["pending" if status == "pending" else "added"] += 1
        return game_id

    def recency(self, app: dict) -> str | None:
        """신작 경로에서 넣으면 안 되는 이유 (C7-f): 'not_game' | 'prerelease' | 'too_old' | None"""
        if not app.get("is_game"):
            return "not_game"
        if is_prerelease(app, self.today):
            return "prerelease"
        if app["release_date"] < self.since:
            return "too_old"
        return None

    def handle_candidate(self, store: str, store_id: str, ios_info: dict, android_chart: set[str],
                         feed: str = "chart") -> str:
        """새 게임 후보 하나 처리. 결과: 'added' | 'pending' | 'updated' | 'skipped' | 'not_game' | 'prerelease' | 'too_old'"""
        if store == "ios":
            if store_id not in ios_info:
                raise LookupError("App Store 조회 결과 없음")
            app = from_ios(ios_info[store_id])
            if feed == "new" and (reason := self.recency(app)):
                return reason
            play, _ = self.pair_android(app, android_chart)
            if play is not None:
                app = combine(play, app)
        else:
            info = self.client.play_details(store_id)
            if info is None:
                raise LookupError("Google Play 조회 결과 없음")
            app = from_play(store_id, info)
            if feed == "new" and (reason := self.recency(app)):
                return reason

        row, how = self.find(app)
        if how in ("id", "name"):  # 이미 있는 게임 (다른 스토어 쪽으로 찾음) → 숫자만 + 빠진 스토어 ID 채움
            if row["review_status"] == "ok":
                self.update_numbers(row, app)
            return "updated"
        if how == "ambiguous":
            game_id = self.insert_game(app, "pending", feed=feed,
                                       similar_to=f"{row['id']} {row['name']} ({row['developer']})")
            return "pending" if game_id else "skipped"
        return "added" if self.insert_game(app, "ok", feed=feed) else "skipped"

    def add_new_games(self, ios_best: dict[str, int], android_best: dict[str, int], ios_info: dict[str, dict]):
        known_android = {r["android_id"] for r in self.games() if r["android_id"]}
        known_ios = {r["ios_id"] for r in self.games() if r["ios_id"]}
        candidates = [(rank, 0, "ios", i) for i, rank in ios_best.items()
                      if i not in known_ios and ios_info.get(i, {}).get("bundleId") not in known_android]
        candidates += [(rank, 1, "android", p) for p, rank in android_best.items() if p not in known_android]
        candidates.sort()
        for _, _, store, store_id in candidates[:CANDIDATE_LIMIT]:
            if self.report["added"] + self.report["pending"] >= self.limit:
                log.info("새 게임 상한 %d개에 도달 → 나머지는 다음 실행 때", self.limit)
                break
            known = {r["android_id"] for r in self.games()} | {r["ios_id"] for r in self.games()}
            if store_id in known:  # 이번 실행에서 다른 스토어 쪽으로 이미 들어옴
                continue
            try:
                self.handle_candidate(store, store_id, ios_info, set(android_best))
                self.conn.commit()
            except Exception as error:  # noqa: BLE001 - 게임 하나 실패해도 계속
                self.conn.rollback()
                self.report["failed"] += 1
                self.note(f"새 게임 실패 {store} {store_id}: {error}")

    # --- 4. 최근 출시작 (C7-f): 차트와 상관없이 출시 90일 이내·출시된 게임은 기준 없이 다 넣음 ---
    def known_ids(self) -> set[str]:
        """DB에 있는 스토어 ID 전부 (검수 대기·제외 포함 → 제외한 게임이 다시 안 들어옴)"""
        rows = self.games()
        return {r["android_id"] for r in rows if r["android_id"]} | {r["ios_id"] for r in rows if r["ios_id"]}

    def new_release_candidates(self) -> tuple[list[tuple[str, str]], dict[str, dict]]:
        """([(store, id)], App Store 조회 결과). App Store 신작 피드(게임만) 먼저, 그다음 Play 검색"""
        known = self.known_ids()
        ios_ids = []
        for kind in IOS_NEW_FEEDS:
            try:
                entries = self.client.ios_new(kind)
                log.info("App Store %s 피드 %d개", kind, len(entries))
                ios_ids += [e["ios_id"] for e in entries]
            except Exception as error:  # noqa: BLE001 - 피드 하나 실패해도 계속
                self.note(f"App Store {kind} 신작 피드 실패: {error}")
        ios_ids = [i for i in dict.fromkeys(ios_ids) if i not in known]
        try:
            ios_info = self.client.ios_lookup(ios_ids) if ios_ids else {}
        except Exception as error:  # noqa: BLE001
            self.note(f"App Store 신작 조회 실패: {error}")
            ios_info = {}
        candidates = []
        for ios_id in ios_ids:
            if ios_id not in ios_info:
                continue
            app = from_ios(ios_info[ios_id])
            if reason := self.recency(app):  # 요청 없이 미리 거름 (게임 아님·출시 예정·90일 넘음)
                self.report[f"new_{reason}"] += 1
                continue
            if app.get("bundle_id") in known:
                continue
            candidates.append(("ios", ios_id))
        self.report["new_from_ios_feed"] = len(candidates)

        packages = []
        for query in load_json("new_release_queries.json")["queries"]:
            try:
                packages += self.client.play_search(query, NEW_QUERY_RESULTS)
            except Exception as error:  # noqa: BLE001
                self.note(f"Play 신작 검색 실패 '{query}': {error}")
        packages = [p for p in dict.fromkeys(packages) if p not in known]
        self.report["new_from_play_search"] = len(packages)
        candidates += [("android", p) for p in packages]
        return candidates, ios_info

    def add_new_releases(self):
        candidates, ios_info = self.new_release_candidates()
        for i, (store, store_id) in enumerate(candidates):
            if self.report["new_added"] + self.report["new_pending"] >= self.new_limit:
                self.report["new_capped"] = len(candidates) - i
                log.info("신작 상한 %d개에 도달 → 남은 후보 %d개는 다음 실행 때", self.new_limit, len(candidates) - i)
                break
            if store_id in self.known_ids():  # 이번 실행에서 다른 스토어 쪽으로 이미 들어옴
                continue
            try:
                result = self.handle_candidate(store, store_id, ios_info, set(), feed="new")
                if result in ("not_game", "prerelease", "too_old"):
                    self.report[f"new_{result}"] += 1
                self.conn.commit()
            except Exception as error:  # noqa: BLE001 - 게임 하나 실패해도 계속
                self.conn.rollback()
                self.report["failed"] += 1
                self.note(f"신작 실패 {store} {store_id}: {error}")

    def report_existing_prerelease(self):
        """이미 들어온 게임 중 출시 예정으로 보이는 것 (지우지 않고 보고만)"""
        rows = self.conn.execute(
            "SELECT id, name, release_date, installs, android_id FROM games WHERE review_status = 'ok' AND "
            "(release_date > ? OR (source = 'auto' AND android_id IS NOT NULL AND COALESCE(installs, 0) = 0))",
            (self.today.isoformat(),)).fetchall()
        self.report["existing_prerelease"] = [f"{r['id']} {r['name']} (출시일 {r['release_date']}, 설치 {r['installs']})"
                                              for r in rows]
        for line in self.report["existing_prerelease"]:
            self.note(f"출시 예정으로 보이는 게임 (지우지 않음): {line}")

    # --- 규모 확장 실험 (C16) ---
    def visible_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM games WHERE review_status = 'ok'").fetchone()[0]

    def genre_queues(self) -> dict[str, list[tuple[str, str]]]:
        """우리 장르 → [(스토어, ID)] — 그 장르 차트들(무료·매출·유료)을 순위대로 번갈아 섞음, 중복 없이"""
        queues = {}
        for genre, charts in GENRE_CHARTS.items():
            lists = []
            for store, category in charts:
                for kind in GENRE_CHART_KINDS:
                    try:
                        if store == "ios":
                            ids = [e["ios_id"] for e in self.client.ios_genre_chart(kind, category)]
                        else:
                            ids = self.client.android_chart(kind, category, GENRE_CHART_SIZE)
                    except Exception as error:  # noqa: BLE001 - 차트 하나 실패해도 계속
                        self.note(f"장르 차트 실패 {store} {category} {kind}: {error}")
                        continue
                    log.info("장르 차트 %s %s %s %d개", genre, category, kind, len(ids))
                    lists.append([(store, i) for i in ids])
            merged = []
            for row in zip_longest(*lists):
                merged += [item for item in row if item]
            queues[genre] = list(dict.fromkeys(merged))
        return queues

    def big_run(self, target: int) -> dict:
        """목표 개수까지: 신작(약 30%) → 장르별 차트(나머지, 장르 돌아가며). 아이콘 안 받음. 기존 게임 숫자 갱신은 안 함"""
        started = time.monotonic()
        ios_best, android_best = self.fetch_charts()           # 홈 인기 순위(chart_rank_*)는 평소처럼
        need = max(0, target - self.visible_count())
        new_quota = need - round(need * CHART_SHARE)
        self.report["big"] = {"target": target, "start_visible": self.visible_count(), "need": need,
                              "new_quota": new_quota}
        self.new_limit = new_quota
        self.add_new_releases()
        chart_quota = max(0, target - self.visible_count())    # 신작이 모자란 만큼 차트가 채움
        queues = self.genre_queues()
        known = self.known_ids()
        ios_ids = sorted({i for q in queues.values() for s, i in q if s == "ios" and i not in known})
        try:
            ios_info = self.client.ios_lookup(ios_ids) if ios_ids else {}
        except Exception as error:  # noqa: BLE001
            self.note(f"App Store 장르 차트 조회 실패: {error}")
            ios_info = {}
        android_all = {i for q in queues.values() for s, i in q if s == "android"} | set(android_best)
        added_by_genre = Counter()
        cap = max(1, int(chart_quota * GENRE_SHARE_MAX))
        positions = {g: 0 for g in queues}
        chart_added = 0
        while chart_added < chart_quota and any(positions[g] < len(queues[g]) for g in queues):
            for genre in queues:
                if chart_added >= chart_quota:
                    break
                if positions[genre] >= len(queues[genre]) or added_by_genre[genre] >= cap:
                    positions[genre] = len(queues[genre]) if added_by_genre[genre] >= cap else positions[genre]
                    continue
                store, store_id = queues[genre][positions[genre]]
                positions[genre] += 1
                if store_id in self.known_ids():
                    continue
                try:
                    result = self.handle_candidate(store, store_id, ios_info, android_all)
                    self.conn.commit()
                except Exception as error:  # noqa: BLE001 - 게임 하나 실패해도 계속
                    self.conn.rollback()
                    self.report["failed"] += 1
                    self.note(f"장르 차트 게임 실패 {store} {store_id}: {error}")
                    continue
                if result == "added":
                    added_by_genre[genre] += 1
                    chart_added += 1
                    if chart_added % 50 == 0:
                        log.info("장르 차트 추가 %d / %d (지금 화면 게임 %d개, %.0f분)", chart_added, chart_quota,
                                 self.visible_count(), (time.monotonic() - started) / 60)
        self.save_chart_ranks()
        self.report["big"].update(chart_quota=chart_quota, chart_added_by_queue=dict(added_by_genre),
                                  queues={g: len(q) for g, q in queues.items()},
                                  queue_used={g: positions[g] for g in queues},
                                  end_visible=self.visible_count(), minutes=round((time.monotonic() - started) / 60, 1))
        return self.report

    def run(self):
        ios_best, android_best = self.fetch_charts()
        ios_ids = sorted(set(ios_best) | {r["ios_id"] for r in self.games() if r["ios_id"]})
        try:
            ios_info = self.client.ios_lookup(ios_ids) if ios_ids else {}
        except Exception as error:  # noqa: BLE001
            self.note(f"App Store 조회 실패: {error}")
            ios_info = {}
        self.refresh_existing(ios_info)
        self.add_new_games(ios_best, android_best, ios_info)
        self.add_new_releases()
        self.save_chart_ranks()
        self.report_existing_prerelease()
        return self.report


# ---------- 실행 ----------
def setup_log(today: date, prefix: str = "sync") -> logging.Handler:
    LOG_DIR.mkdir(exist_ok=True)
    handler = logging.FileHandler(LOG_DIR / f"{prefix}_{today:%Y%m%d}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    return handler


def last_success(conn) -> datetime | None:
    row = conn.execute("SELECT MAX(started_at) FROM sync_runs WHERE status = 'ok'").fetchone()
    return datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) if row and row[0] else None


def run_sync(db_path=None, client=None, limit: int = NEW_GAME_LIMIT, today: date | None = None,
             new_limit: int = NEW_RELEASE_MAX_PER_RUN, target: int | None = None) -> dict:
    """수집 1번. 다른 수집이 돌고 있으면 건너뜀. 결과 요약을 돌려준다.
    target을 주면 규모 확장 실험(C16): 장르별 차트로 그 개수까지, 아이콘 안 받음 — main()이 실험 DB일 때만 넘김"""
    today = today or date.today()
    handler = setup_log(today, "sync_big" if target else "sync")
    try:
        with get_connection(db_path) as conn:
            stale = (datetime.now(timezone.utc) - timedelta(hours=RUNNING_STALE_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
            if conn.execute("SELECT 1 FROM sync_runs WHERE status = 'running' AND started_at > ?", (stale,)).fetchone():
                log.info("다른 수집이 실행 중 → 건너뜀")
                return {"skipped_run": True}
            started = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            run_id = conn.execute("INSERT INTO sync_runs (started_at, status) VALUES (?, 'running')",
                                  (started,)).lastrowid
            conn.commit()
        log.info("수집 시작 (차트 새 게임 상한 %d, 신작 상한 %d)", limit, new_limit)
        report, status = {}, "failed"
        try:
            with get_connection(db_path) as conn:
                sync = Sync(conn, client or StoreClient(), limit, today, new_limit, save_icons=target is None)
                report = sync.big_run(target) if target else sync.run()
                report["snapshot"] = save_daily_snapshot(conn, today)  # 인기 급상승용 하루 기록 (C12-e)
                conn.commit()
            status = "ok"
            return report
        finally:
            get = report.get
            with get_connection(db_path) as conn:
                conn.execute(
                    "UPDATE sync_runs SET finished_at = ?, status = ?, added = ?, updated = ?, pending = ?, failed = ?, "
                    "note = ? WHERE id = ?",
                    (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), status,
                     get("added", 0) + get("new_added", 0), get("updated", 0), get("pending", 0) + get("new_pending", 0),
                     get("failed", 0), "\n".join(get("notes", []))[:4000] or None, run_id))
            log.info("수집 끝 (%s): 차트 추가 %s, 갱신 %s, 검수 대기 %s, 실패 %s, 장르 제외 %s", status,
                     get("added", 0), get("updated", 0), get("pending", 0), get("failed", 0), get("skipped", 0))
            log.info("신작: 추가 %s (iOS만 %s / Android만 %s / 두 스토어 %s), 검수 대기 %s, 출시 예정 제외 %s, "
                     "90일 넘음 %s, 게임 아님 %s, 상한으로 미룸 %s (후보: App Store 피드 %s, Play 검색 %s)",
                     get("new_added", 0), get("new_ios_only", 0), get("new_android_only", 0), get("new_both", 0),
                     get("new_pending", 0), get("new_prerelease", 0), get("new_too_old", 0), get("new_not_game", 0),
                     get("new_capped", 0), get("new_from_ios_feed", 0), get("new_from_play_search", 0))
    finally:
        log.removeHandler(handler)
        handler.close()


def prune_new(conn, min_installs: int | None, min_ratings: int | None, dry_run: bool = False) -> list[str]:
    """
    신작 경로(source_feed='new')로 들어온 게임 중 기준 미달을 숨김 (review_status='rejected', 지우지 않음) (C7-f).
    설치 수 기준은 설치 수를 아는 게임(Android)에만, 별점 수는 없으면 0으로 본다. 숨긴(숨길) 게임 id 목록
    """
    conditions, params = [], []
    if min_installs is not None:
        conditions.append("(installs IS NOT NULL AND installs < ?)")
        params.append(min_installs)
    if min_ratings is not None:
        conditions.append("COALESCE(store_rating_count, 0) < ?")
        params.append(min_ratings)
    if not conditions:
        raise ValueError("--min-installs 또는 --min-ratings 중 하나는 있어야 함")
    where = f"source_feed = 'new' AND review_status = 'ok' AND ({' OR '.join(conditions)})"
    ids = [r[0] for r in conn.execute(f"SELECT id FROM games WHERE {where}", params)]
    if not dry_run:
        conn.execute(f"UPDATE games SET review_status = 'rejected' WHERE {where}", params)
    return ids


def recheck_pending(conn, dry_run: bool = False) -> list[dict]:
    """
    검수 대기 게임을 C8-a 규칙으로 다시 판정: DB의 다른 게임(제외된 것 빼고)과 이름이 비슷하지 않으면 → ok (짝 없이 그대로).
    비슷한 게임이 있으면 그대로 검수 대기. [{id, name, developer, status, similar}] (status = 'ok' | 'pending')
    """
    rows = conn.execute("SELECT id, name, developer FROM games WHERE review_status != 'rejected'").fetchall()
    result = []
    for game in conn.execute("SELECT id, name, developer FROM games WHERE review_status = 'pending' ORDER BY rowid"):
        similar = [f"{r['id']} {r['name']} ({r['developer']})" for r in rows
                   if r["id"] != game["id"] and name_similarity(r["name"], game["name"]) >= NAME_MATCH]
        status = "pending" if similar else "ok"
        if status == "ok" and not dry_run:
            conn.execute("UPDATE games SET review_status = 'ok' WHERE id = ?", (game["id"],))
        result.append({"id": game["id"], "name": game["name"], "developer": game["developer"], "status": status,
                       "similar": similar})
    return result


def set_review(ids: list[str], status: str):
    with get_connection() as conn:
        for game_id in ids:
            changed = conn.execute("UPDATE games SET review_status = ? WHERE id = ? AND source = 'auto'",
                                   (status, game_id)).rowcount
            print(f"{game_id}: {'→ ' + status if changed else '자동 수집 게임이 아님 (바꾸지 않음)'}")


def big_collect(db: Path, target: int, client=None) -> dict:
    """규모 확장 실험 (C16). 실제 DB·seed에는 쓰지 않음"""
    db = db.resolve()
    if db in {DB_PATH.resolve(), (DATA_DIR / "seed.db").resolve()}:
        sys.exit(f"실험 DB에 실제 DB를 줄 수 없습니다: {db}")
    if not db.exists():
        src = sqlite3.connect(f"{DB_PATH.resolve().as_uri()}?mode=ro", uri=True)  # 원본은 읽기 전용
        dst = sqlite3.connect(db)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        log.info("실험 DB 만듦: %s ← %s", db, DB_PATH)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(console)
    try:
        report = run_sync(db, client, target=target)
    finally:
        log.removeHandler(console)
    big = report.get("big", {})
    print(f"\n실험 DB {db}: 화면 게임 {big.get('start_visible')} → {big.get('end_visible')} (목표 {target}), "
          f"신작 {report.get('new_added', 0)} · 차트 {report.get('added', 0)} · 검수 대기 "
          f"{report.get('pending', 0) + report.get('new_pending', 0)} · 실패 {report.get('failed', 0)} · {big.get('minutes')}분")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="스토어 자동 수집 (C7-a2, C7-f)")
    parser.add_argument("--limit", type=int, default=NEW_GAME_LIMIT, help="차트 새 게임 최대 개수")
    parser.add_argument("--new-limit", type=int, default=NEW_RELEASE_MAX_PER_RUN, help="최근 출시작 최대 개수")
    parser.add_argument("--pending", action="store_true", help="검수 대기 목록")
    parser.add_argument("--approve", nargs="+", metavar="ID")
    parser.add_argument("--reject", nargs="+", metavar="ID")
    parser.add_argument("--set-tier", nargs=2, metavar=("ID", "TIER"))
    parser.add_argument("--prune-new", action="store_true", help="신작 경로 게임 중 기준 미달을 숨김 (지우지 않음)")
    parser.add_argument("--min-installs", type=int)
    parser.add_argument("--min-ratings", type=int)
    parser.add_argument("--recheck-pending", action="store_true",
                        help="검수 대기를 다시 판정 (DB 게임과 이름이 안 비슷하면 화면에 보이게)")
    parser.add_argument("--dry-run", action="store_true", help="--prune-new·--recheck-pending: 보여 주기만 하고 안 바꿈")
    # 규모 확장 실험 (C16): 기본은 모두 꺼짐 — 실제 서버의 매일 수집(app/store_sync.py)은 이 옵션을 안 씀
    parser.add_argument("--db", type=Path, help="실험 DB (없으면 data/gamepick.db를 읽기 전용으로 복사해 만듦)")
    parser.add_argument("--target", type=int, help="--db: 화면에 보이는 게임이 이 개수가 되도록 (예: 1500)")
    parser.add_argument("--genre-charts", action="store_true", help="--db·--target: 장르별 인기 차트로 모음")
    args = parser.parse_args(argv)
    if args.db or args.target or args.genre_charts:
        if not (args.db and args.target and args.genre_charts):
            sys.exit("규모 실험은 --db, --target, --genre-charts를 함께 줘야 합니다 (실험 DB 전용)")
        return big_collect(args.db, args.target)
    if not DB_PATH.exists():
        sys.exit("DB가 없습니다. 먼저 python scripts/init_db.py 를 실행하세요.")
    if args.recheck_pending:
        with get_connection() as conn:
            for item in recheck_pending(conn, args.dry_run):
                verb = ("→ ok 예정" if args.dry_run else "→ ok") if item["status"] == "ok" else "그대로 검수 대기"
                print(f"{item['id']:30} {item['name']} ({item['developer']}) {verb}"
                      + (f" — 비슷한 DB 게임: {'; '.join(item['similar'])}" if item["similar"] else ""))
        return
    if args.prune_new:
        if args.min_installs is None and args.min_ratings is None:
            sys.exit("--prune-new에는 --min-installs 또는 --min-ratings가 필요합니다.")
        with get_connection() as conn:
            ids = prune_new(conn, args.min_installs, args.min_ratings, args.dry_run)
        print(f"{'숨길' if args.dry_run else '숨김'} {len(ids)}개: {', '.join(ids)}")
        return
    if args.pending:
        with get_connection() as conn:
            for row in conn.execute("SELECT id, name, developer, android_id, ios_id FROM games "
                                    "WHERE review_status = 'pending' ORDER BY rowid"):
                print(f"{row['id']:38} {row['name']} ({row['developer']}) android={row['android_id']} ios={row['ios_id']}")
        return
    if args.approve or args.reject:
        set_review(args.approve or [], "ok")
        set_review(args.reject or [], "rejected")
        return
    if args.set_tier:
        game_id, tier = args.set_tier
        if tier not in ("대형", "중견", "소형"):
            sys.exit("규모는 대형 / 중견 / 소형 중 하나")
        with get_connection() as conn:
            changed = conn.execute("UPDATE games SET tier = ?, tier_locked = 1 WHERE id = ?", (tier, game_id)).rowcount
        print(f"{game_id}: {'규모 ' + tier + ' (자동 분류가 안 덮어씀)' if changed else '없는 게임'}")
        return
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(console)
    report = run_sync(limit=args.limit, new_limit=args.new_limit)
    if report.get("notes"):
        print(f"\n확인할 것 {len(report['notes'])}건 → logs/sync_{date.today():%Y%m%d}.log")


if __name__ == "__main__":
    main()

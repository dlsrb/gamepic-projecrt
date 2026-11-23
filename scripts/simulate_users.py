"""
가상 사용자 시뮬레이션 (PROJECT.md 4.11 C14, docs/C14_시뮬_지시.md) — **로직 점검용, 실제 사용자 피드백 아님**.

- data/gamepick.db를 data/sim.db로 복사해서 **그 사본에서만** 돈다. 실제 DB·seed·배포 zip에는 한 줄도 안 들어감
  (DB_PATH 환경 변수를 sim.db로 둔 뒤 앱을 불러오고, 앱이 sim.db를 보는지 확인한 다음에만 시작)
- 앱은 실제 API로 부른다 (FastAPI TestClient, 사용자마다 쿠키 따로) → 실제 사용자와 같은 경로
- 요청 횟수 제한은 RATE_LIMITS=off (이 프로세스에서만). 스토어 자동 수집·백업·아이콘 받기는 안 돎 (lifespan 안 씀)
- 가상 사용자 표시: sim.db에만 만드는 표 sim_users (실제 DB 구조는 안 바꿈)
- 외부 AI 호출 없음. 행동은 규칙 + 무작위(--seed 고정, 해시 시드도 고정해서 같은 시드면 같은 결과)
- 날짜: 하루 단위로 돌리고, 그날 생긴 행(사용자·반응·댓글·댓글 추천·검색 기록)의 created_at을 그날로 옮김 (14일째 = 오늘)

실행: python scripts/simulate_users.py --users 300 --seed 42 --days 14 [--force]
      → data/sim.db, data/sim_metrics.json(숫자 원본), docs/sim_report.md(보고서)
      python scripts/simulate_users.py --report-only   숫자 원본으로 보고서만 다시 (docs/sim_notes.md 고친 뒤)
"""
import argparse
import json
import os
import random
import sqlite3
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = BASE_DIR / "data" / "gamepick.db"
DEFAULT_OUT = BASE_DIR / "data" / "sim.db"
DEFAULT_METRICS = BASE_DIR / "data" / "sim_metrics.json"
DEFAULT_REPORT = BASE_DIR / "docs" / "sim_report.md"
NOTES = BASE_DIR / "docs" / "sim_notes.md"   # 사람이 쓴 분석 (있으면 보고서 8번에 붙임)

HOME_LIMIT, HOME_MIN_MATCH = 9, 60           # static/js/home.js와 같은 값
BENCH_REPEAT = 20                            # 속도 측정: 엔드포인트마다 요청 수
COMMENT_P = 0.075                            # 상세를 열 때 댓글을 쓸 확률 (300명이면 대략 300개)
VOTE_P = 0.2                                 # 남의 댓글에 추천·비추천할 확률
SEARCH_P = 0.3                               # 방문당 검색 확률
RANKING_PAGE_P = 0.3                         # 방문당 랭킹 페이지(장르 탭)까지 볼 확률
OTHER_OPEN_P = 0.2                           # 이달의 게임·랭킹에서 상세를 하나 더 열 확률
NONE_DISLIKE_P = 0.2                         # 설문 4번 "딱히 없어요"
OPTIONAL_ANSWER_P = 0.5                      # 선택 문항에 답할 확률
BADGE_WORDS = ["인디", "신작", "급상승", "인기"]

# ---------- 페르소나 (취향 유형) ----------
# q3·q4·q6 = 설문에 밝히는 것, hidden_* = 숨은 진짜 취향 (반응 확률은 이걸로 → 엔진이 맞혔는지 따로 봄)
PERSONAS = [
    {"key": "rpg_collector", "label": "RPG 수집러", "share": 15,
     "q3": ["genre:RPG", "subgenre:수집형RPG"], "q4": ["genre:퍼즐"], "q6": None,
     "hidden_like": {"genre:RPG", "subgenre:수집형RPG", "subgenre:턴제RPG", "mood:판타지", "view:애니풍"},
     "hidden_dislike": {"genre:퍼즐", "genre:캐주얼"}, "words": ["RPG", "수집형RPG", "판타지", "애니"]},
    {"key": "puzzle_healing", "label": "퍼즐·힐링", "share": 12,
     "q3": ["genre:퍼즐", "mood:힐링"], "q4": ["play:PvP"], "q6": "no_ads",
     "hidden_like": {"genre:퍼즐", "mood:힐링", "mood:귀여움", "play:짧은플레이", "subgenre:감성퍼즐"},
     "hidden_dislike": {"play:PvP", "mood:다크판타지"}, "words": ["퍼즐", "힐링", "귀여운", "짧게"]},
    {"key": "pvp", "label": "경쟁 PvP", "share": 10,
     "q3": ["play:PvP", "genre:액션"], "q4": ["genre:퍼즐", "mood:힐링"], "q6": None,
     "hidden_like": {"play:PvP", "play:멀티", "genre:액션", "genre:슈팅", "play:빠른템포"},
     "hidden_dislike": {"genre:퍼즐", "mood:힐링"}, "words": ["PvP", "대전", "멀티", "슈팅"]},
    {"key": "indie_mood", "label": "인디·감성", "share": 10,
     "q3": ["mood:감성", "mood:스토리중심"], "q4": ["play:PvP", "play:자동전투"], "q6": "no_gacha",
     "hidden_like": {"mood:감성", "mood:스토리중심", "view:픽셀아트", "view:2D", "play:싱글"},
     "hidden_dislike": {"play:PvP", "play:자동전투", "bm:가챠"}, "words": ["인디", "감성", "스토리", "도트"]},
    {"key": "idle", "label": "방치형", "share": 10,
     "q3": ["subgenre:방치형RPG", "play:자동전투"], "q4": [], "q6": None,
     "hidden_like": {"subgenre:방치형RPG", "play:자동전투", "genre:시뮬레이션", "play:짧은플레이"},
     "hidden_dislike": set(), "words": ["방치", "자동", "키우기"]},
    {"key": "strategy", "label": "전략·SLG", "share": 8,
     "q3": ["genre:전략", "subgenre:실시간전략"], "q4": ["mood:귀여움"], "q6": None,
     "hidden_like": {"genre:전략", "subgenre:실시간전략", "subgenre:기지건설", "mood:밀리터리"},
     "hidden_dislike": {"mood:귀여움"}, "words": ["전략", "밀리터리", "실시간전략"]},
    {"key": "casual", "label": "캐주얼 가볍게", "share": 12,
     "q3": ["genre:캐주얼", "play:짧은플레이"], "q4": ["genre:RPG"], "q6": "no_ads",
     "hidden_like": {"genre:캐주얼", "play:짧은플레이", "play:오프라인", "mood:귀여움", "genre:퍼즐"},
     "hidden_dislike": {"genre:RPG", "play:PvP"}, "words": ["캐주얼", "짧은", "오프라인", "무료"]},
    {"key": "broad", "label": "넓은 취향", "share": 10,
     "q3": ["genre:RPG", "genre:액션", "genre:시뮬레이션", "genre:어드벤처"], "q4": [], "q6": None,
     "hidden_like": {"mood:판타지", "mood:스토리중심", "view:3D", "genre:어드벤처"},
     "hidden_dislike": set(), "words": ["RPG", "어드벤처", "신작", "급상승"]},
    {"key": "f2p", "label": "무과금 선호", "share": 6,
     "q3": ["genre:RPG", "genre:전략"], "q4": [], "q6": "no_both",
     "hidden_like": {"bm:무과금가능", "genre:RPG", "play:싱글"},
     "hidden_dislike": {"bm:가챠", "bm:광고"}, "words": ["무과금", "무료", "혼자"]},
    {"key": "picky", "label": "까다로운 (비선호 많음)", "share": 7,
     "q3": ["genre:어드벤처", "mood:스토리중심"],
     "q4": ["genre:캐주얼", "genre:퍼즐", "play:PvP", "play:자동전투", "mood:귀여움"], "q6": "no_both",
     "hidden_like": {"mood:스토리중심", "genre:어드벤처", "play:싱글"},
     "hidden_dislike": {"genre:캐주얼", "genre:퍼즐", "play:PvP", "play:자동전투", "mood:귀여움"},
     "words": ["어드벤처", "스토리", "싱글"]},
]

POSITIVE = ["{tag} 좋아하면 잘 맞아요", "{tag}라서 출퇴근에 하기 좋아요", "{tag} 느낌이 마음에 들어요",
            "생각보다 {tag} 요소가 잘 살아 있어요", "{tag} 좋아해서 계속 하고 있어요"]
NEGATIVE = ["{tag}는 제 취향이 아니었어요", "과금 유도가 좀 있어요", "초반은 괜찮은데 금방 질려요",
            "광고가 자주 나와요", "{tag} 쪽을 기대했는데 아쉬웠어요"]


# ---------- 준비 ----------
def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="가상 사용자 시뮬레이션 (sim.db에서만)")
    parser.add_argument("--users", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--force", action="store_true", help="sim.db가 있으면 묻지 않고 덮어씀")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="복사할 원본 DB (읽기만)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="시뮬 DB (덮어씀)")
    parser.add_argument("--metrics", type=Path, default=DEFAULT_METRICS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--no-bench", action="store_true", help="속도 측정 건너뜀 (테스트용)")
    parser.add_argument("--report-only", action="store_true",
                        help="시뮬을 다시 돌리지 않고 --metrics 숫자로 보고서만 다시 씀 (docs/sim_notes.md를 고친 뒤)")
    return parser.parse_args(argv)


def rerun_with_hash_seed(seed: int) -> None:
    """같은 --seed면 같은 결과가 나오게 파이썬 해시 시드도 고정 (집합 순서). 다르면 같은 인자로 다시 실행"""
    if os.environ.get("PYTHONHASHSEED") == str(seed):
        return
    env = dict(os.environ, PYTHONHASHSEED=str(seed))
    sys.exit(subprocess.run([sys.executable, *sys.argv], env=env).returncode)


def prepare_db(source: Path, out: Path, force: bool) -> None:
    source, out = source.resolve(), out.resolve()
    real = {DEFAULT_SOURCE.resolve(), (BASE_DIR / "data" / "seed.db").resolve()}
    if out in real or out == source:
        raise SystemExit(f"시뮬 DB를 실제 DB에 쓸 수 없습니다: {out}")
    if out.exists() and not force:
        if not sys.stdin.isatty():
            raise SystemExit(f"{out}가 이미 있습니다. 덮어쓰려면 --force")
        if input(f"{out}가 이미 있습니다. 덮어쓸까요? [y/N] ").strip().lower() != "y":
            raise SystemExit("그만둠")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    src = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)   # 원본은 읽기 전용으로만 연다
    dst = sqlite3.connect(out)
    try:
        src.backup(dst)
        dst.execute("CREATE TABLE sim_users (user_id TEXT PRIMARY KEY, kind TEXT NOT NULL, persona TEXT)")
        dst.commit()
    finally:
        src.close()
        dst.close()


def boot_app(out: Path):
    """sim.db를 보게 환경 변수를 정한 뒤에만 앱을 불러온다. 다른 DB를 보면 멈춤"""
    os.environ.update(DB_PATH=str(out.resolve()), RATE_LIMITS="off", STORE_SYNC="false", ICON_FETCH="false",
                      BACKUP="false")
    sys.path.insert(0, str(BASE_DIR))
    from app import config
    from app.main import app
    if Path(config.DB_PATH).resolve() != out.resolve() or config.RATE_LIMITS_ON:
        raise SystemExit(f"앱이 sim.db가 아닌 {config.DB_PATH}를 보고 있어 멈춤")
    return app


# ---------- 시뮬레이터 ----------
class Sim:
    def __init__(self, app, args, rng: random.Random):
        from fastapi.testclient import TestClient
        self.TestClient, self.app, self.args, self.rng = TestClient, app, args, rng
        self.db = sqlite3.connect(args.out)
        self.db.row_factory = sqlite3.Row
        self.load_games()
        self.users: list[dict] = []
        self.impressions, self.monthly_seen, self.searches, self.reacted = [], [], [], []
        self.comments = self.votes = 0
        self.like_attempts = Counter()

    # --- 데이터 ---
    def load_games(self):
        from app.reco import repository as repo
        from app.reco.recommender import game_features
        games = repo.load_games(self.db)
        rows = {r["id"]: r for r in self.db.execute("SELECT id, tag_source, source, tags_checked FROM games")}
        self.features = {g["id"]: set(game_features(g)) for g in games}
        self.names = {g["id"]: g["name"] for g in games}
        self.tag_source = {g["id"]: rows[g["id"]]["tag_source"] for g in games}
        self.genre = {g["id"]: g["genre"] for g in games}

    def client(self):
        return self.TestClient(self.app)

    def mark(self, client, kind: str, persona: str | None = None) -> str:
        uid = client.cookies.get("gp_uid")
        self.db.execute("INSERT OR REPLACE INTO sim_users (user_id, kind, persona) VALUES (?, ?, ?)", (uid, kind, persona))
        self.db.commit()
        return uid

    def affinity(self, user: dict, game_id: str) -> tuple[int, int]:
        feats = self.features.get(game_id, set())
        return len(feats & user["persona"]["hidden_like"]), len(feats & user["persona"]["hidden_dislike"])

    # --- 설문 (실제 설문 API) ---
    def answers_for(self, persona: dict, questions: list[dict]) -> dict:
        rng = self.rng
        by_id = {q["id"]: q for q in questions}
        values = {qid: {o["value"] for o in q["options"]} for qid, q in by_id.items()}
        answers = {"q1": [rng.choice(["android", "ios", "both"])], "q2": [rng.choice(["free_only", "paid_ok", "any"])]}
        if answers["q2"] == ["paid_ok"] and rng.random() < OPTIONAL_ANSWER_P:
            answers["q2_1"] = [rng.choice(sorted(values["q2_1"]))]
        answers["q3"] = [v for v in persona["q3"] if v in values["q3"]]
        q4 = [v for v in persona["q4"] if v in values["q4"]]
        answers["q4"] = ["none"] if (not q4 or rng.random() < NONE_DISLIKE_P) else q4
        candidates = [o for o in by_id["q5"]["options"] if not o.get("exclusive")]
        liked = persona["hidden_like"]
        scored = sorted(candidates, key=lambda o: (-len(set(o.get("features", [])) & liked), o["value"]))
        pool = [o["value"] for o in scored[:15] if set(o.get("features", [])) & liked]
        answers["q5"] = rng.sample(pool, min(len(pool), rng.randint(0, 3)))
        if persona["q6"]:
            answers["q6"] = [persona["q6"]]
        for qid in ("q7", "q8", "q9", "q10", "q11", "q12", "q13", "q14"):
            if qid in by_id and rng.random() < OPTIONAL_ANSWER_P:
                options = sorted(o["value"] for o in by_id[qid]["options"] if not o.get("exclusive"))
                answers[qid] = [rng.choice(options)]
        return answers

    def declared_excluded(self, uid: str) -> set[str]:
        """사용자가 설문에서 밝힌 비선호 + 연관 태그 (앱 규칙 그대로, 위반 검사용)"""
        from app import services
        from app.reco import repository as repo
        survey = repo.load_survey(self.db, uid)
        return services.expand_dislikes(set(survey.get("dislikes", [])))

    def create_user(self, index: int, questions: list[dict]) -> dict:
        persona = self.persona_for(index)
        client = self.client()
        client.post("/api/users")
        uid = self.mark(client, "user", persona["key"])
        answers = self.answers_for(persona, questions)
        response = client.post("/api/survey/answers", json={"answers": answers})
        if response.status_code != 200:
            raise RuntimeError(f"설문 실패 {response.status_code} {response.text} {answers}")
        group = self.db.execute("SELECT exp_group FROM users WHERE id = ?", (uid,)).fetchone()[0]
        return {"uid": uid, "client": client, "persona": persona, "group": group, "visit": 0,
                "excluded": self.declared_excluded(uid), "answers": answers}

    def persona_for(self, index: int) -> dict:
        return self.persona_plan[index]

    def plan_personas(self, n: int) -> None:
        """비율대로 n명 배정 (반올림 차이는 비율 큰 유형부터 채움), 그 뒤 섞음"""
        counts = {p["key"]: int(n * p["share"] / 100) for p in PERSONAS}
        for p in sorted(PERSONAS, key=lambda p: -p["share"]):
            if sum(counts.values()) >= n:
                break
            counts[p["key"]] += 1
        plan = [p for p in PERSONAS for _ in range(counts[p["key"]])]
        self.rng.shuffle(plan)
        self.persona_plan = plan
        self.persona_counts = counts

    # --- 방문 ---
    def weighted_pick(self, user: dict, game_ids: list[str], k: int) -> list[str]:
        pool = list(dict.fromkeys(game_ids))
        picked = []
        while pool and len(picked) < k:
            weights = []
            for gid in pool:
                hits, bad = self.affinity(user, gid)
                weights.append(0.3 if bad else 1 + 2 * hits)
            gid = self.rng.choices(pool, weights=weights)[0]
            picked.append(gid)
            pool.remove(gid)
        return picked

    def react(self, user: dict, game_id: str, source: str) -> dict:
        client, rng = user["client"], self.rng
        client.get(f"/api/games/{game_id}")
        hits, bad = self.affinity(user, game_id)
        if bad:
            p_like, p_store, p_no = 0.02, 0.01, 0.45
        elif hits >= 2:
            p_like, p_store, p_no = 0.45, 0.25, 0.03
        elif hits == 1:
            p_like, p_store, p_no = 0.2, 0.1, 0.08
        else:
            p_like, p_store, p_no = 0.04, 0.02, 0.25
        done = {"like": False, "store": False, "dislike": False}
        for kind, p in (("like", p_like), ("store", p_store), ("dislike", p_no)):
            if rng.random() < p:
                client.post("/api/reactions", json={"game_id": game_id, "type": kind})
                done[kind] = True
                if kind == "like":
                    self.like_attempts[(user["uid"], game_id)] += 1
        self.reacted.append({"uid": user["uid"], "group": user["group"], "game": game_id, "source": source, **done})
        if rng.random() < COMMENT_P:
            self.write_comment(user, game_id, hits, bad)
        if rng.random() < VOTE_P * 2:
            self.vote_comments(user, game_id, hits, bad)
        return done

    def write_comment(self, user: dict, game_id: str, hits: int, bad: int) -> None:
        tags = sorted(f.split(":", 1)[1] for f in self.features.get(game_id, set()) if not f.startswith("bm:"))
        tag = self.rng.choice(tags) if tags else self.genre.get(game_id, "이 게임")
        template = self.rng.choice(NEGATIVE if bad or not hits else POSITIVE)
        response = user["client"].post(f"/api/games/{game_id}/comments", json={"body": template.format(tag=tag)})
        if response.status_code == 201:
            self.comments += 1

    def vote_comments(self, user: dict, game_id: str, hits: int, bad: int) -> None:
        listed = user["client"].get(f"/api/games/{game_id}/comments", params={"sort": "top", "limit": 10}).json()
        for comment in listed.get("comments", []):
            if comment["mine"] or self.rng.random() >= VOTE_P:
                continue
            value = 1 if (hits and not bad and self.rng.random() < 0.8) else -1
            if user["client"].put(f"/api/comments/{comment['id']}/vote", json={"value": value}).status_code == 200:
                self.votes += 1

    def search(self, user: dict) -> None:
        rng, persona = self.rng, user["persona"]
        roll = rng.random()
        target = None
        if roll < 0.5:
            kind, query = "word", " ".join(rng.sample(persona["words"], min(len(persona["words"]), rng.randint(1, 2))))
        elif roll < 0.75:
            liked = sorted(g for g in self.names if self.affinity(user, g)[0] > 0) or sorted(self.names)
            target = rng.choice(liked)
            name = self.names[target].split()[0]
            kind, query = "name", name[:max(2, min(len(name), rng.randint(2, 4)))]
        else:
            kind = "badge"
            query = rng.choice(BADGE_WORDS) + (" " + rng.choice(persona["words"]) if rng.random() < 0.5 else "")
        data = user["client"].get("/api/search", params={"q": query}).json()
        ids = [g["id"] for g in data.get("games", [])]
        self.searches.append({"uid": user["uid"], "kind": kind, "query": query, "count": len(ids),
                              "target": target, "found": target in ids if target else None})

    def visit(self, user: dict) -> None:
        rng, client = self.rng, user["client"]
        user["visit"] += 1
        # 홈 한 번 = 맞춤 추천 + 이달의 게임 + 인기 랭킹 5 + 추천 순위 (home.js가 부르는 것)
        feed = client.get("/api/feed", params={"section": "match", "limit": HOME_LIMIT, "min_match": HOME_MIN_MATCH}).json()
        monthly = client.get("/api/monthly").json().get("games", [])
        popular = client.get("/api/popular", params={"tab": "all", "limit": 5, "fill": "false"}).json().get("games", [])
        client.get("/api/ranking")
        home = feed.get("games", [])
        opened = set(self.weighted_pick(user, [g["id"] for g in home], rng.randint(1, 3)))
        results = {gid: self.react(user, gid, "home") for gid in opened}
        for position, g in enumerate(home):
            hits, bad = self.affinity(user, g["id"])
            r = results.get(g["id"], {})
            self.impressions.append({
                "uid": user["uid"], "group": user["group"], "persona": user["persona"]["key"], "visit": user["visit"],
                "game": g["id"], "slot": g.get("slot"), "position": position, "fallback": feed.get("fallback", False),
                "hits": hits, "bad": bad, "violation": bool(self.features.get(g["id"], set()) & user["excluded"]),
                "tag_source": self.tag_source.get(g["id"]), "opened": g["id"] in opened,
                "like": r.get("like", False), "store": r.get("store", False), "dislike": r.get("dislike", False)})
        for g in monthly:
            self.monthly_seen.append({"uid": user["uid"], "game": g["id"],
                                      "violation": bool(self.features.get(g["id"], set()) & user["excluded"])})
        others = [g["id"] for g in monthly + popular]
        if rng.random() < RANKING_PAGE_P:
            genre = next((f.split(":", 1)[1] for f in sorted(user["persona"]["hidden_like"]) if f.startswith("genre:")), None)
            tab = genre if genre in ("RPG", "시뮬레이션", "액션") else "all"
            others += [g["id"] for g in client.get("/api/popular", params={"tab": tab}).json().get("games", [])[:10]]
        if others and rng.random() < OTHER_OPEN_P:
            for gid in self.weighted_pick(user, [g for g in others if g not in opened], 1):
                self.react(user, gid, "other")
        if rng.random() < SEARCH_P:
            self.search(user)

    # --- 날짜 옮기기 ---
    def stamp(self) -> str:
        return self.db.execute("SELECT datetime('now', '-1 second')").fetchone()[0]

    def shift(self, since: str, days_back: int) -> None:
        if days_back <= 0:
            return
        offset = f"-{days_back} days"
        for table in ("users", "reactions", "comments", "comment_votes", "search_logs"):
            self.db.execute(f"UPDATE {table} SET created_at = datetime(created_at, ?) WHERE created_at >= ?", (offset, since))
        self.db.commit()

    # --- 속도·랭킹 ---
    def bench(self, client, game_id: str) -> dict:
        if self.args.no_bench:
            return {}
        endpoints = {
            "홈 맞춤 추천": ("/api/feed", {"section": "match", "limit": HOME_LIMIT, "min_match": HOME_MIN_MATCH}),
            "이달의 게임": ("/api/monthly", {}),
            "인기 랭킹": ("/api/popular", {"tab": "all"}),
            "추천 순위": ("/api/ranking", {}),
            "검색": ("/api/search", {"q": "RPG 판타지"}),
            "상세": (f"/api/games/{game_id}", {}),
        }
        result = {}
        for name, (path, params) in endpoints.items():
            times = []
            for _ in range(BENCH_REPEAT):
                start = time.perf_counter()
                client.get(path, params=params)
                times.append((time.perf_counter() - start) * 1000)
            times.sort()
            result[name] = {"median_ms": round(statistics.median(times), 1),
                            "p95_ms": round(times[int(len(times) * 0.95) - 1], 1)}
        return result

    def snapshot(self, probe_client) -> dict:
        likes = probe_client.get("/api/ranking").json().get("ranking", [])
        monthly = probe_client.get("/api/monthly").json().get("games", [])
        return {"likes_top10": [(g["rank"], g["name"], g["like_count"]) for g in likes[:10]],
                "monthly": [g["name"] for g in monthly]}

    # --- 전체 ---
    def run(self) -> dict:
        args, rng = self.args, self.rng
        questions = self.client().get("/api/survey/questions").json()["questions"]
        # 측정용 사용자(가상) — 설문 RPG·판타지, 처음과 끝에 같은 사람으로 속도·랭킹을 잼
        bench = self.client()
        bench.post("/api/users")
        self.mark(bench, "bench")
        bench.post("/api/survey/answers", json={"answers": {"q1": ["both"], "q2": ["any"], "q3": ["genre:RPG", "mood:판타지"],
                                                            "q4": ["none"], "q5": []}})
        probe = self.client()   # 설문 안 한 사람 (이달의 게임 기본 구성)
        probe.post("/api/users")
        self.mark(probe, "probe")
        bench_game = sorted(self.names)[0]
        before = {"bench": self.bench(bench, bench_game), **self.snapshot(probe)}
        real_users = self.db.execute("SELECT COUNT(*) FROM users WHERE id NOT IN (SELECT user_id FROM sim_users)").fetchone()[0]

        self.plan_personas(args.users)
        schedule = defaultdict(list)                     # 날 → 사용자 번호
        for i in range(args.users):
            n_visits = rng.randint(1, 10)
            for day in sorted(rng.sample(range(1, args.days + 1), min(n_visits, args.days))):
                schedule[day].append(i)
        created: dict[int, dict] = {}
        for day in range(1, args.days + 1):
            since = self.stamp()
            todays = list(schedule[day])
            rng.shuffle(todays)
            for i in todays:
                if i not in created:
                    created[i] = self.create_user(i, questions)
                self.visit(created[i])
            self.shift(since, args.days - day)
        self.users = list(created.values())
        after = {"bench": self.bench(bench, bench_game), **self.snapshot(probe)}
        return self.metrics(before, after, real_users)

    # --- 숫자 모으기 ---
    def metrics(self, before: dict, after: dict, real_users: int) -> dict:
        imp = self.impressions
        visible = self.db.execute("SELECT COUNT(*) FROM games WHERE review_status = 'ok'").fetchone()[0]

        def rates(rows):
            n = len(rows)
            return {"impressions": n,
                    "like_rate": round(sum(r["like"] for r in rows) / n, 4) if n else None,
                    "dislike_rate": round(sum(r["dislike"] for r in rows) / n, 4) if n else None,
                    "hidden_match": round(sum(1 for r in rows if r["hits"] and not r["bad"]) / n, 4) if n else None,
                    "hidden_bad": round(sum(1 for r in rows if r["bad"]) / n, 4) if n else None}

        groups = sorted({r["group"] for r in imp})
        by_group = {g: rates([r for r in imp if r["group"] == g]) for g in groups}
        by_visit = {g: {v: rates([r for r in imp if r["group"] == g and r["visit"] == v])
                        for v in range(1, 11) if any(r["group"] == g and r["visit"] == v for r in imp)} for g in groups}
        by_persona = {p["key"]: rates([r for r in imp if r["persona"] == p["key"]]) for p in PERSONAS}
        exposure = Counter(r["game"] for r in imp)
        top10 = exposure.most_common(10)
        slot_stats = {}
        for slot in ("추천", "탐색"):
            rows = [r for r in imp if r["slot"] == slot]
            slot_stats[slot] = {"impressions": len(rows), "distinct": len({r["game"] for r in rows})}
        sources = {}
        for src in ("human", "ai", "store", "auto"):
            rows = [r for r in imp if r["tag_source"] == src]
            sources[src] = {**rates(rows), "share": round(len(rows) / len(imp), 4) if imp else None}
        name_searches = [s for s in self.searches if s["kind"] == "name"]
        zero = Counter(s["query"] for s in self.searches if s["count"] == 0)
        duplicate_likes = self.db.execute(
            "SELECT COUNT(*) FROM (SELECT user_id, game_id FROM reactions WHERE type = 'like' "
            "GROUP BY user_id, game_id HAVING COUNT(*) > 1)").fetchone()[0]
        repeated_like_tries = sum(1 for n in self.like_attempts.values() if n > 1)
        days = [r[0] for r in self.db.execute(
            "SELECT DISTINCT date(created_at) FROM reactions WHERE user_id IN (SELECT user_id FROM sim_users) ORDER BY 1")]
        return {
            "simulation": True,
            "args": {"users": self.args.users, "seed": self.args.seed, "days": self.args.days},
            "real_users_in_copy": real_users,
            "personas": [{"key": p["key"], "label": p["label"], "share": p["share"], "count": self.persona_counts[p["key"]]}
                         for p in PERSONAS],
            "groups_count": dict(Counter(u["group"] for u in self.users)),
            "visits": sum(u["visit"] for u in self.users),
            "survey_none_dislike": sum(1 for u in self.users if u["answers"]["q4"] == ["none"]),
            "home": {"by_group": by_group, "by_visit": by_visit, "by_persona": by_persona,
                     "fallback_rate": round(sum(1 for r in imp if r["position"] == 0 and r["fallback"]) /
                                            max(1, sum(1 for r in imp if r["position"] == 0)), 4)},
            "violations": {"home": sum(r["violation"] for r in imp), "home_impressions": len(imp),
                           "monthly": sum(r["violation"] for r in self.monthly_seen),
                           "monthly_impressions": len(self.monthly_seen)},
            "diversity": {"visible_games": visible, "distinct_in_home": len(exposure),
                          "top10": [(self.names.get(g, g), n) for g, n in top10],
                          "top10_share": round(sum(n for _, n in top10) / len(imp), 4) if imp else None,
                          "slots": slot_stats},
            "tag_source": sources,
            "rankings": {"before": {k: before[k] for k in ("likes_top10", "monthly")},
                         "after": {k: after[k] for k in ("likes_top10", "monthly")}},
            "search": {"total": len(self.searches), "by_kind": dict(Counter(s["kind"] for s in self.searches)),
                       "zero_result": zero.most_common(),
                       "name_success": round(sum(1 for s in name_searches if s["found"]) / len(name_searches), 4)
                       if name_searches else None,
                       "name_failures": sorted({(s["query"], self.names.get(s["target"], s["target"]))
                                                for s in name_searches if not s["found"]})},
            "comments": self.comments, "comment_votes": self.votes,
            "reactions": {k: sum(r[k] for r in self.reacted) for k in ("like", "store", "dislike")},
            "details_opened": len(self.reacted),
            "duplicate_like_rows": duplicate_likes, "repeated_like_tries": repeated_like_tries,
            "days_with_reactions": len(days), "first_day": days[0] if days else None, "last_day": days[-1] if days else None,
            "speed": {"before": before["bench"], "after": after["bench"]},
        }


# ---------- 보고서 ----------
def pct(x) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def write_report(m: dict, path: Path) -> None:
    group_label = {"random": "random (무작위 순서)", "survey": "survey (설문만)", "feedback": "feedback (설문 + 반응 반영)"}
    L = ["# 게임픽 가상 사용자 시뮬레이션 보고서", "",
         "> **시뮬레이션 결과입니다. 실제 사용자 피드백이 아닙니다.** 규칙과 무작위(시드 고정)로 움직이는 가상 사용자로 "
         "추천·랭킹·검색·댓글 로직이 많은 사용자에서도 제대로 도는지 점검한 것.",
         f"> 실행: `python scripts/simulate_users.py --users {m['args']['users']} --seed {m['args']['seed']} "
         f"--days {m['args']['days']}` — 실제 DB의 사본 `data/sim.db`에서만 (실제 DB·seed·배포에는 가상 데이터 없음)", "",
         "## 0. 설정", "",
         f"- 가상 사용자 {m['args']['users']}명 (사본에 원래 있던 실제 사용자 {m['real_users_in_copy']}명은 그대로 둠), "
         f"{m['args']['days']}일, 방문 합계 {m['visits']}회 (사람마다 1~10회)",
         f"- 실험 그룹 (기존 무작위 배정): " + ", ".join(f"{k} {v}명" for k, v in sorted(m["groups_count"].items())),
         f"- 설문 4번 \"딱히 없어요\": {m['survey_none_dislike']}명 / 상세 열람 {m['details_opened']}회, "
         f"따봉 {m['reactions']['like']} · 스토어 이동 {m['reactions']['store']} · 관심 없음 {m['reactions']['dislike']}, "
         f"댓글 {m['comments']}개, 댓글 추천·비추천 {m['comment_votes']}회, 검색 {m['search']['total']}회",
         f"- 반응·댓글 날짜: {m['first_day']} ~ {m['last_day']} ({m['days_with_reactions']}일에 나눠 기록)", "",
         "| 취향 유형 | 비율 | 인원 |", "|---|---|---|"]
    L += [f"| {p['label']} | {p['share']}% | {p['count']} |" for p in m["personas"]]
    L += ["", "반응 확률은 화면의 취향 %가 아니라 **유형의 숨은 진짜 취향 태그**와 게임 태그가 몇 개 겹치는지로 정함 "
          "(2개 이상 겹치면 따봉 45% · 1개 20% · 0개 4%, 숨은 비선호 태그가 있으면 따봉 2%·관심 없음 45%). "
          "\"숨은 취향 일치\" = 추천된 게임에 숨은 선호 태그가 1개 이상 있고 숨은 비선호 태그가 없는 비율", "",
          "## 1. 추천 적중 (홈 맞춤 추천)", "",
          "| 그룹 | 노출 | 따봉률 | 관심 없음률 | 숨은 취향 일치 | 숨은 비선호 포함 |", "|---|---|---|---|---|---|"]
    for g, r in m["home"]["by_group"].items():
        L.append(f"| {group_label.get(g, g)} | {r['impressions']} | {pct(r['like_rate'])} | {pct(r['dislike_rate'])} | "
                 f"{pct(r['hidden_match'])} | {pct(r['hidden_bad'])} |")
    L += ["", f"- 홈이 대체 동작(60% 기준 미달)으로 간 방문 비율: {pct(m['home']['fallback_rate'])}", "",
          "**방문 회차별 숨은 취향 일치율** (그룹별, 괄호는 노출 수)", "",
          "| 회차 | " + " | ".join(m["home"]["by_visit"]) + " |", "|---|" + "---|" * len(m["home"]["by_visit"])]
    for v in range(1, 11):
        cells = []
        for g in m["home"]["by_visit"]:
            r = m["home"]["by_visit"][g].get(str(v)) or m["home"]["by_visit"][g].get(v)
            cells.append(f"{pct(r['hidden_match'])} ({r['impressions']})" if r else "-")
        if any(c != "-" for c in cells):
            L.append(f"| {v} | " + " | ".join(cells) + " |")
    L += ["", "**유형별**", "", "| 유형 | 노출 | 따봉률 | 숨은 취향 일치 | 숨은 비선호 포함 |", "|---|---|---|---|---|"]
    labels = {p["key"]: p["label"] for p in m["personas"]}
    for key, r in m["home"]["by_persona"].items():
        L.append(f"| {labels[key]} | {r['impressions']} | {pct(r['like_rate'])} | {pct(r['hidden_match'])} | {pct(r['hidden_bad'])} |")
    v = m["violations"]
    L += ["", "## 2. 비선호 위반 (설문에 밝힌 비선호 + 연관 태그)", "",
          f"- 홈 맞춤 추천: **{v['home']}회** / 노출 {v['home_impressions']}회",
          f"- 이달의 게임: **{v['monthly']}회** / 노출 {v['monthly_impressions']}회",
          "- 0이어야 정상" + (" ✅" if v["home"] == 0 and v["monthly"] == 0 else " ⚠"), "",
          "## 3. 다양성·쏠림", "",
          f"- 홈 추천에 한 번이라도 나온 게임: **{m['diversity']['distinct_in_home']}개 / 화면에 보이는 게임 "
          f"{m['diversity']['visible_games']}개**",
          f"- 상위 10개 게임이 홈 노출에서 차지한 비율: **{pct(m['diversity']['top10_share'])}**",
          "- 칸별: " + ", ".join(f"{k} 칸 노출 {s['impressions']} · 서로 다른 게임 {s['distinct']}"
                                 for k, s in m["diversity"]["slots"].items()), "",
          "| 순위 | 게임 | 홈 노출 수 |", "|---|---|---|"]
    L += [f"| {i} | {name} | {n} |" for i, (name, n) in enumerate(m["diversity"]["top10"], 1)]
    L += ["", "## 4. 태그 출처별", "", "| 태그 출처 | 홈 노출 비율 | 노출 | 따봉률 | 숨은 취향 일치 |", "|---|---|---|---|---|"]
    for src, r in m["tag_source"].items():
        L.append(f"| {src} | {pct(r['share'])} | {r['impressions']} | {pct(r['like_rate'])} | {pct(r['hidden_match'])} |")
    rk = m["rankings"]
    L += ["", "## 5. 랭킹·이달의 게임 변화 (시뮬 전 → 후)", "", "**추천 순위 (따봉 수) 상위 10**", "",
          "| 순위 | 시뮬 전 | 시뮬 후 |", "|---|---|---|"]
    for i in range(10):
        b = rk["before"]["likes_top10"][i] if i < len(rk["before"]["likes_top10"]) else None
        a = rk["after"]["likes_top10"][i] if i < len(rk["after"]["likes_top10"]) else None
        L.append(f"| {i + 1} | {f'{b[1]} ({b[2]})' if b else '-'} | {f'{a[1]} ({a[2]})' if a else '-'} |")
    L += ["", "**이달의 게임 (설문 안 한 사람 기준)**", "",
          f"- 전: {', '.join(rk['before']['monthly']) or '-'}", f"- 후: {', '.join(rk['after']['monthly']) or '-'}", "",
          "## 6. 검색", "",
          f"- 검색 {m['search']['total']}회 (" + ", ".join(f"{k} {n}" for k, n in m["search"]["by_kind"].items()) +
          ") — word = 유형에 맞는 단어, name = 게임 이름 일부, badge = 인디·신작·급상승·인기",
          f"- 이름 일부 검색 성공률 (찾으려던 게임이 결과에 있음): **{pct(m['search']['name_success'])}**", "",
          "**결과 0개였던 검색어**", ""]
    L += [f"- `{q}` × {n}" for q, n in m["search"]["zero_result"]] or ["- 없음"]
    if m["search"]["name_failures"]:
        L += ["", "**이름 일부 검색 실패 (검색어 → 찾으려던 게임)**", ""]
        L += [f"- `{q}` → {name}" for q, name in m["search"]["name_failures"][:20]]
    sp = m["speed"]
    L += ["", "## 7. 속도 (같은 측정용 가상 사용자, 엔드포인트마다 20번, 로컬 TestClient)", ""]
    if sp["before"]:
        L += ["| API | 실제 사용자 4명 (중앙값 / 95%) | 시뮬 후 (중앙값 / 95%) |", "|---|---|---|"]
        for name in sp["before"]:
            b, a = sp["before"][name], sp["after"][name]
            L.append(f"| {name} | {b['median_ms']} / {b['p95_ms']} ms | {a['median_ms']} / {a['p95_ms']} ms |")
    else:
        L.append("- 이번 실행은 측정 안 함 (--no-bench)")
    L += ["", "## 8. 발견한 문제", "", "**자동 점검**", "",
          f"- 같은 게임 따봉 중복 행: {m['duplicate_like_rows']}개 (같은 게임에 따봉을 두 번 이상 누른 경우 "
          f"{m['repeated_like_tries']}번 — API가 한 번만 기록해야 0)",
          f"- 비선호 위반: 홈 {v['home']} · 이달의 게임 {v['monthly']}",
          f"- 결과 0개 검색어: {len(m['search']['zero_result'])}종류", ""]
    if NOTES.exists():
        L += ["**분석 (Code가 숫자를 보고 쓴 것, `docs/sim_notes.md`)**", "", NOTES.read_text(encoding="utf-8").strip(), ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def main(argv=None):
    args = parse_args(argv)
    if args.report_only:
        write_report(json.loads(args.metrics.read_text(encoding="utf-8")), args.report)
        print(f"보고서만 다시 씀 → {args.report}")
        return
    rerun_with_hash_seed(args.seed)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    prepare_db(args.source, args.out, args.force)
    app = boot_app(args.out)
    rng = random.Random(args.seed)
    random.seed(args.seed)   # 앱 안의 무작위(실험 그룹 배정·탐색 칸)도 같은 시드로
    started = time.perf_counter()
    sim = Sim(app, args, rng)
    metrics = sim.run()
    metrics["elapsed_seconds"] = round(time.perf_counter() - started, 1)
    sim.db.close()
    args.metrics.parent.mkdir(parents=True, exist_ok=True)
    args.metrics.write_text(json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
    write_report(metrics, args.report)
    print(f"시뮬레이션 끝 ({metrics['elapsed_seconds']}초): 가상 사용자 {args.users}명, 방문 {metrics['visits']}회 "
          f"→ {args.out}, {args.metrics}, {args.report}")


if __name__ == "__main__":
    main()

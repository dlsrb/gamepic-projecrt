"""
게임픽 추천 엔진 (콘텐츠 기반, 2단계: 후보 거르기 → 순위 매기기)

설문(survey) 형식 예시:
{
    "genres": ["RPG"],                      # 대분류 장르. 고르면 이 장르만 후보로 남는다(강한 필터)
    "subgenres": ["턴제RPG"],               # 세부 장르 선호
    "likes": {"view": ["2D"], "mood": ["다크판타지"], "play": ["짧은플레이"]},
    "dislikes": ["bm:가챠", "play:PvP"],    # 하나라도 가진 게임은 후보에서 제외
    "favorites": ["dnfm"],                  # 좋아하는 게임 id. 그 게임의 태그가 취향이 된다
}
"""
import json
import math

from config import (
    DATA_PATH, CATEGORY_WEIGHTS, SURVEY_WEIGHT, FAVORITE_WEIGHT,
    FEEDBACK_DELTA, WEIGHT_MIN, WEIGHT_MAX, EXPLORE_RATIO,
)

FEATURE_FIELDS = ["genre", "subgenre", "view", "play", "mood", "bm"]


# ---------- 데이터 ----------
def load_games(path=DATA_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def game_features(game: dict) -> list[str]:
    """게임 하나를 '종류:태그' 목록으로 바꾼다. 예: ['genre:RPG', 'subgenre:턴제RPG', 'view:2D', ...]"""
    feats = []
    for cat in FEATURE_FIELDS:
        values = game.get(cat, [])
        if isinstance(values, str):
            values = [values]
        feats += [f"{cat}:{v}" for v in values]
    return feats


def category_of(feature: str) -> str:
    return feature.split(":", 1)[0]


def label_of(feature: str) -> str:
    return feature.split(":", 1)[1]


# ---------- 사용자 취향 ----------
class UserProfile:
    def __init__(self):
        self.genres: set[str] = set()      # 강한 필터용 대분류 장르
        self.weights: dict[str, float] = {}  # '종류:태그' -> 가중치
        self.excluded: set[str] = set()    # 비선호 태그 (강한 제외)
        self.seen: set[str] = set()        # 이미 보여준/알고 있는 게임

    def add(self, feature: str, delta: float) -> None:
        w = self.weights.get(feature, 0.0) + delta
        self.weights[feature] = max(WEIGHT_MIN, min(WEIGHT_MAX, w))

    def top_weights(self, n: int = 10) -> list[tuple[str, float]]:
        return sorted(self.weights.items(), key=lambda x: -x[1])[:n]


def build_profile(survey: dict, games_by_id: dict[str, dict]) -> UserProfile:
    p = UserProfile()

    for g in survey.get("genres", []):
        p.genres.add(g)
        p.add(f"genre:{g}", SURVEY_WEIGHT)
    for sg in survey.get("subgenres", []):
        p.add(f"subgenre:{sg}", SURVEY_WEIGHT)
    for cat, tags in survey.get("likes", {}).items():
        for t in tags:
            p.add(f"{cat}:{t}", SURVEY_WEIGHT)

    p.excluded = set(survey.get("dislikes", []))

    # 좋아하는 게임: 그 게임의 태그를 전부 취향에 더한다.
    # 장르를 직접 고르지 않았다면 장르는 '약한 선호'로만 들어간다(필터 X).
    for gid in survey.get("favorites", []):
        game = games_by_id.get(gid)
        if game is None:
            continue
        for f in game_features(game):
            p.add(f, FAVORITE_WEIGHT)
        p.seen.add(gid)  # 이미 아는 게임이니 추천에서는 뺀다

    return p


# ---------- 점수 ----------
def score(game: dict, profile: UserProfile) -> float:
    """가중 코사인 유사도 (-1 ~ 1). 태그 수가 많은 게임이 유리해지지 않도록 정규화한다."""
    feats = game_features(game)
    if not feats or not profile.weights:
        return 0.0
    dot = sum(profile.weights.get(f, 0.0) * CATEGORY_WEIGHTS[category_of(f)] ** 2 for f in feats)
    user_norm = math.sqrt(sum((w * CATEGORY_WEIGHTS[category_of(f)]) ** 2 for f, w in profile.weights.items()))
    game_norm = math.sqrt(sum(CATEGORY_WEIGHTS[category_of(f)] ** 2 for f in feats))
    if user_norm == 0 or game_norm == 0:
        return 0.0
    return dot / (user_norm * game_norm)


def reasons(game: dict, profile: UserProfile, top: int = 3) -> list[str]:
    """점수에 가장 크게 기여한 태그 = 추천 이유"""
    contrib = []
    for f in game_features(game):
        c = profile.weights.get(f, 0.0) * CATEGORY_WEIGHTS[category_of(f)] ** 2
        if c > 0:
            contrib.append((c, label_of(f)))
    contrib.sort(reverse=True)
    return [name for _, name in contrib[:top]]


# ---------- 추천 ----------
def passes_filter(game: dict, profile: UserProfile) -> bool:
    if game["id"] in profile.seen:
        return False
    if profile.genres and game["genre"] not in profile.genres:
        return False
    if set(game_features(game)) & profile.excluded:
        return False
    return True


def recommend(games: list[dict], profile: UserProfile, k: int = 10,
              explore_ratio: float = EXPLORE_RATIO) -> list[dict]:
    # 1단계: 후보 거르기
    candidates = [g for g in games if passes_filter(g, profile)]
    # 2단계: 순위 매기기
    scored = sorted(
        ({"game": g, "score": score(g, profile)} for g in candidates),
        key=lambda x: -x["score"],
    )

    n_explore = round(k * explore_ratio) if explore_ratio > 0 else 0
    n_main = k - n_explore

    main = scored[:n_main]
    for item in main:
        item["slot"] = "추천"

    # 탐색 슬롯: 후보(=장르 필터 통과) 안에서, 아직 안 뽑힌 인디 게임 중 점수가 양수인 것
    rest = scored[n_main:]
    explore = [x for x in rest if x["game"].get("indie") and x["score"] > 0][:n_explore]
    for item in explore:
        item["slot"] = "탐색"
    # 인디가 부족하면 남은 후보로 채운다
    if len(explore) < n_explore:
        picked = {id(x) for x in explore}
        filler = [x for x in rest if id(x) not in picked][: n_explore - len(explore)]
        for item in filler:
            item["slot"] = "탐색"
        explore += filler

    result = main + explore
    for item in result:
        item["reasons"] = reasons(item["game"], profile)
        item["match"] = round(max(0.0, item["score"]) * 100)
    return result


def similar_games(game_id: str, games: list[dict], k: int = 5) -> list[dict]:
    """'이 게임과 비슷한 게임' — 상세 페이지 하단용"""
    by_id = {g["id"]: g for g in games}
    profile = build_profile({"favorites": [game_id]}, by_id)
    return recommend(games, profile, k=k, explore_ratio=0)


# ---------- 피드 반응 ----------
def apply_feedback(profile: UserProfile, game: dict, action: str) -> None:
    """action: 'like' | 'skip' | 'click'"""
    delta = FEEDBACK_DELTA[action]
    for f in game_features(game):
        profile.add(f, delta)
    profile.seen.add(game["id"])

"""
게임픽 추천 엔진 (콘텐츠 기반, 2단계: 후보 거르기 → 순위 매기기)
프로토타입 gamepick_reco/recommender.py를 옮긴 것. DB 읽기·쓰기는 repository.py가 맡고,
이 파일은 게임 목록과 사용자 취향(UserProfile)만 받아 계산한다.

설문(survey) 형식 예시:
{
    "genres": ["RPG"],                      # 대분류 장르. 고르면 이 장르만 후보로 남는다(강한 필터)
    "subgenres": ["턴제RPG"],               # 세부 장르 선호
    "likes": {"view": ["2D"], "mood": ["다크판타지"], "play": ["짧은플레이"]},
    "dislikes": ["bm:가챠", "play:PvP"],    # 비선호 태그. 점수 −5 + 이 태그를 가진 게임은 후보에서 제외
    "favorites": ["dnfm"],                  # 좋아하는 게임 id. 그 게임의 태그가 취향이 된다
    "boost": ["genre:퍼즐"],                # 점수만 더함 (장르 필터에는 안 넣음)
    "require": ["play:오프라인"],           # 이 태그가 없는 게임은 후보에서 제외 (꼭 오프라인)
}
설문 문항 응답 → 이 형식 변환은 survey_map.py.
"""
import math

from app.reco.reco_config import (
    CATEGORY_WEIGHTS, INITIAL_SCORE, SURVEY_WEIGHT, FAVORITE_WEIGHT, DISLIKE_WEIGHT,
    FEEDBACK_DELTA, WEIGHT_MIN, WEIGHT_MAX, EXPLORE_RATIO,
    MATCH_COVERAGE_RATIO, MATCH_RELATIVE_RATIO, MATCH_NEGATIVE_PENALTY, MATCH_MAX, MATCH_MIN_TAGS,
)

FEATURE_FIELDS = ["genre", "subgenre", "view", "play", "mood", "bm"]


# ---------- 데이터 ----------
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


def clamp(score: float) -> float:
    return max(WEIGHT_MIN, min(WEIGHT_MAX, score))


# ---------- 사용자 취향 ----------
class UserProfile:
    def __init__(self):
        self.genres: set[str] = set()      # 강한 필터용 대분류 장르
        self.weights: dict[str, float] = {}  # '종류:태그' -> 태그 점수
        self.excluded: set[str] = set()    # 비선호 태그 (강한 제외)
        self.required: set[str] = set()    # 꼭 있어야 하는 태그 (없으면 제외)
        self.seen: set[str] = set()        # 이미 보여준/알고 있는 게임
        self.best_score: float | None = None  # 후보 중 가장 높은 score() (취향 % 계산용, set_best_score로 채움)

    def add(self, feature: str, delta: float) -> None:
        self.weights[feature] = clamp(self.weights.get(feature, INITIAL_SCORE) + delta)

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
    for f in survey.get("boost", []):
        p.add(f, SURVEY_WEIGHT)
    p.required = set(survey.get("require", []))

    # 좋아하는 게임: 그 게임의 태그를 전부 취향에 더한다(겹치면 합산).
    # 장르를 직접 고르지 않았다면 장르는 '약한 선호'로만 들어간다(필터 X).
    for gid in survey.get("favorites", []):
        game = games_by_id.get(gid)
        if game is None:
            continue
        for f in game_features(game):
            p.add(f, FAVORITE_WEIGHT)
        p.seen.add(gid)  # 이미 아는 게임이니 추천에서는 뺀다

    # 비선호는 다른 선택과 겹쳐도 비선호가 이긴다 (더하지 않고 −5로 덮어씀)
    for f in survey.get("dislikes", []):
        p.weights[f] = DISLIKE_WEIGHT
        p.excluded.add(f)

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


def coverage(game: dict, profile: UserProfile) -> float:
    """
    게임 쪽 일치율 (0~1): 이 게임 태그 중 내가 좋아하는 게 얼마나 되나 (싫어하는 태그는 깎음).
    태그가 MATCH_MIN_TAGS개보다 적으면 그 비율만큼 깎는다 (C7-f: 장르 하나만 맞아도 높게 나오던 문제)
    """
    feats = game_features(game)
    total = sum(CATEGORY_WEIGHTS[category_of(f)] for f in feats)
    if total == 0:
        return 0.0
    liked = sum(CATEGORY_WEIGHTS[category_of(f)] for f in feats if profile.weights.get(f, 0.0) > 0)
    disliked = sum(CATEGORY_WEIGHTS[category_of(f)] for f in feats if profile.weights.get(f, 0.0) < 0)
    ratio = min(1.0, max(0.0, (liked - disliked * MATCH_NEGATIVE_PENALTY) / total))
    return ratio * min(1.0, len(feats) / MATCH_MIN_TAGS)


def set_best_score(profile: UserProfile, games: list[dict]) -> None:
    """이 사용자 후보(passes_filter) 중 가장 높은 score(). 요청마다 한 번 (취향 %의 '1등 대비' 기준)"""
    profile.best_score = max((score(g, profile) for g in games if passes_filter(g, profile)), default=0.0)


def match_percent(game: dict, profile: UserProfile) -> int:
    """
    화면에 보여 줄 취향 부합도 % (0~MATCH_MAX). 추천 순서(score)와 따로 계산 (C7-e)
    = 일치율 × 0.7 + 내 1등 게임 대비 × 0.3. 프로필이 비었거나 1등 점수를 모르거나 0이면 일치율만
    """
    cov = coverage(game, profile)
    best = profile.best_score
    if not profile.weights or not best or best <= 0:
        value = cov
    else:
        relative = min(1.0, max(0.0, score(game, profile) / best))
        value = MATCH_COVERAGE_RATIO * cov + MATCH_RELATIVE_RATIO * relative
    return min(MATCH_MAX, round(100 * value))


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
    features = set(game_features(game))
    if features & profile.excluded:
        return False
    if not profile.required <= features:
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
    """action: 'like' | 'store' | 'dislike'. 남용 방지(따봉 1회, 스토어 하루 1회)는 repository.py에서 거른다."""
    delta = FEEDBACK_DELTA[action]
    for f in game_features(game):
        profile.add(f, delta)
    profile.seen.add(game["id"])

"""
기존 기획서 공식 (비교용). 모든 태그를 같은 1점으로 센다.
  N = max(게임 태그 수, 3), Y = min(선호 태그 수, N)
  취향 부합도 = 일치 수 / Y * 100,  비선호 태그 하나라도 있으면 제외
"""
from recommender import game_features, label_of


def flat_tags(game: dict) -> set[str]:
    return {label_of(f) for f in game_features(game)}


def survey_to_flat(survey: dict, games_by_id: dict) -> tuple[list[str], set[str]]:
    """새 방식 설문을 기존 방식(태그 목록)으로 바꾼다. 좋아하는 게임은 그 게임 태그를 선호로 넣는다."""
    liked = list(survey.get("genres", [])) + list(survey.get("subgenres", []))
    for tags in survey.get("likes", {}).values():
        liked += tags
    for gid in survey.get("favorites", []):
        if gid in games_by_id:
            liked += sorted(flat_tags(games_by_id[gid]))
    liked = list(dict.fromkeys(liked))  # 중복 제거, 순서 유지
    disliked = {d.split(":", 1)[-1] for d in survey.get("dislikes", [])}
    return liked, disliked


def baseline_recommend(games: list[dict], survey: dict, k: int = 10) -> list[dict]:
    by_id = {g["id"]: g for g in games}
    liked, disliked = survey_to_flat(survey, by_id)
    seen = set(survey.get("favorites", []))
    results = []
    for g in games:
        if g["id"] in seen:
            continue
        tags = flat_tags(g)
        if tags & disliked:
            continue
        n = max(len(tags), 3)
        y = min(len(liked), n)
        match = len(set(liked) & tags)
        s = (match / y * 100) if y else 0
        results.append({"game": g, "match": round(s), "slot": "추천"})
    results.sort(key=lambda x: -x["match"])
    return results[:k]

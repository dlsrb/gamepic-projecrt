"""
정답 세트 테스트: "이걸 입력하면 상위 5개에 이게 나와야 하고, 이건 나오면 안 된다"
새 추천 방식과 기존 공식을 같은 케이스로 비교한다.

케이스를 추가하려면 TEST_CASES에 딕셔너리 하나만 더 넣으면 된다.
  expect_any   : 상위 TOP_N 안에 이 중 하나 이상 있어야 통과
  expect_all   : 상위 TOP_N 안에 이게 전부 있어야 통과
  forbid_genres: 상위 TOP_N 안에 이 장르가 있으면 실패 (엉뚱한 추천)

게임은 DB(data/gamepick.db)에서 읽는다. 먼저 scripts/init_db.py를 실행할 것.
실행: python scripts/evaluate.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DB_PATH  # noqa: E402
from app.db import get_connection  # noqa: E402
from app.reco.recommender import build_profile, recommend  # noqa: E402
from app.reco.repository import load_games  # noqa: E402
from scripts.baseline import baseline_recommend  # noqa: E402

TOP_N = 5

TEST_CASES = [
    {
        "name": "정통 RPG를 원함",
        "survey": {"genres": ["RPG"], "subgenres": ["턴제RPG"]},
        "expect_any": ["dq8", "ff6", "chrono"],
        "forbid_genres": ["슈팅", "퍼즐", "전략"],
    },
    {
        "name": "2D RPG를 원함",
        "survey": {"genres": ["RPG"], "likes": {"view": ["2D"]}},
        "expect_all": ["dnfm", "maplem"],
        "forbid_genres": ["슈팅", "퍼즐"],
    },
    {
        "name": "2D 횡스크롤 RPG를 원함",
        "survey": {"genres": ["RPG"], "likes": {"view": ["2D", "횡스크롤"]}},
        "expect_all": ["dnfm", "maplem"],
        "forbid_genres": ["슈팅"],
    },
    {
        "name": "[카스 문제] RPG + 멀티 + 빠른템포 + 무과금",
        "survey": {"genres": ["RPG"], "likes": {"play": ["멀티", "빠른템포"], "bm": ["무과금가능"]}},
        "expect_any": ["dnfm"],
        "forbid_genres": ["슈팅", "전략", "액션"],
    },
    {
        "name": "던파를 좋아함 (장르 직접 선택 X)",
        "survey": {"favorites": ["dnfm"]},
        "expect_any": ["maplem"],
        "forbid_genres": ["슈팅", "퍼즐"],
    },
    {
        "name": "RPG인데 가챠는 싫음",
        "survey": {"genres": ["RPG"], "dislikes": ["bm:가챠"]},
        "expect_any": ["dq8", "dnfm", "maplem", "ff6", "chrono", "darkest"],
        "forbid_ids": ["genshin", "hsr", "epic7", "summoners", "bluearchive", "lineagem", "odin"],
    },
    {
        "name": "다크판타지 로그라이크",
        "survey": {"likes": {"play": ["로그라이크"], "mood": ["다크판타지"]}},
        "expect_all": ["darkest", "deadcells"],
        "forbid_genres": ["퍼즐", "슈팅"],
    },
    {
        "name": "짧게 하는 퍼즐",
        "survey": {"genres": ["퍼즐"], "likes": {"play": ["짧은플레이"]}},
        "expect_any": ["candy", "anipang4", "royalmatch"],
        "forbid_genres": ["RPG", "슈팅"],
    },
    {
        "name": "스타듀 밸리를 좋아함",
        "survey": {"favorites": ["stardew"]},
        "expect_any": ["minecraft", "gris", "alto", "monument"],
        "forbid_genres": ["슈팅"],
    },
]


def check(case: dict, results: list[dict]) -> tuple[bool, list[str]]:
    top = results[:TOP_N]
    ids = [r["game"]["id"] for r in top]
    problems = []
    if "expect_any" in case and not set(case["expect_any"]) & set(ids):
        problems.append("기대 게임 없음")
    if "expect_all" in case:
        missing = [x for x in case["expect_all"] if x not in ids]
        if missing:
            problems.append(f"빠짐: {', '.join(missing)}")
    bad = [r["game"]["name"] for r in top if r["game"]["genre"] in case.get("forbid_genres", [])]
    bad += [r["game"]["name"] for r in top if r["game"]["id"] in case.get("forbid_ids", [])]
    if bad:
        problems.append(f"엉뚱한 추천: {', '.join(bad)}")
    return (not problems), problems


def show(results: list[dict]) -> str:
    return " / ".join(f"{r['game']['name']}({r['match']}%)" for r in results[:TOP_N])


def main():
    if not DB_PATH.exists():
        sys.exit("DB가 없습니다. 먼저 python scripts/init_db.py 를 실행하세요.")
    with get_connection() as conn:
        games = load_games(conn)
    by_id = {g["id"]: g for g in games}
    passed_new = passed_old = 0
    off_new = off_old = 0

    for i, case in enumerate(TEST_CASES, 1):
        profile = build_profile(case["survey"], by_id)
        new = recommend(games, profile, k=TOP_N, explore_ratio=0)
        old = baseline_recommend(games, case["survey"], k=TOP_N)

        ok_new, prob_new = check(case, new)
        ok_old, prob_old = check(case, old)
        passed_new += ok_new
        passed_old += ok_old
        off_new += sum(r["game"]["genre"] in case.get("forbid_genres", []) for r in new[:TOP_N])
        off_old += sum(r["game"]["genre"] in case.get("forbid_genres", []) for r in old[:TOP_N])

        print(f"\n[{i}] {case['name']}")
        print(f"  새 방식  {'✅' if ok_new else '❌'} {show(new)}")
        if prob_new:
            print(f"           → {'; '.join(prob_new)}")
        print(f"  기존공식 {'✅' if ok_old else '❌'} {show(old)}")
        if prob_old:
            print(f"           → {'; '.join(prob_old)}")

    n = len(TEST_CASES)
    print("\n" + "=" * 60)
    print(f"새 방식 : {passed_new}/{n} 통과, 엉뚱한 장르 {off_new}개 (상위 {TOP_N} × {n}케이스 중)")
    print(f"기존공식: {passed_old}/{n} 통과, 엉뚱한 장르 {off_old}개")


if __name__ == "__main__":
    main()

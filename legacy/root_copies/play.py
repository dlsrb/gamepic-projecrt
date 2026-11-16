"""
직접 실험하는 놀이터: 설문 → 추천 → 피드 반응(관심/넘김/스토어) → 추천이 어떻게 바뀌는지 확인
PyCharm에서 이 파일을 Run 하면 된다.
"""
from recommender import load_games, build_profile, recommend, apply_feedback

FEED_SIZE = 6  # 한 화면에 보여줄 게임 수 (그중 일부는 탐색 슬롯)

DISLIKE_OPTIONS = ["bm:가챠", "bm:광고", "play:PvP", "play:자동전투", "play:멀티", "view:실사풍"]
FAMOUS_GAMES = ["dq8", "ff6", "dnfm", "maplem", "lineagem", "genshin", "hsr", "bluearchive",
                "summoners", "pubgm", "brawl", "clashroyale", "wildrift", "candy",
                "stardew", "minecraft", "cookierun", "pokemongo"]


def unique_values(games, field, only_genres=None):
    vals = []
    for g in games:
        if only_genres and g["genre"] not in only_genres:
            continue
        v = g[field] if isinstance(g[field], list) else [g[field]]
        for x in v:
            if x not in vals:
                vals.append(x)
    return vals


def ask_multi(title, options, labels=None):
    labels = labels or options
    print(f"\n■ {title}  (번호를 쉼표로, 엔터=건너뛰기)")
    for i, lab in enumerate(labels, 1):
        print(f"  {i:>2}. {lab}")
    raw = input("  > ").strip()
    if not raw:
        return []
    picked = []
    for tok in raw.replace(" ", "").split(","):
        if tok.isdigit() and 1 <= int(tok) <= len(options):
            picked.append(options[int(tok) - 1])
    return picked


def run_survey(games):
    by_id = {g["id"]: g for g in games}
    print("=" * 50)
    print(" 게임픽 취향 설문")
    print("=" * 50)

    genres = ask_multi("관심 있는 장르 (고르면 이 장르만 추천)", unique_values(games, "genre"))
    subgenres = ask_multi("세부 장르", unique_values(games, "subgenre", genres or None))
    fav_ids = [x for x in FAMOUS_GAMES if x in by_id]
    favorites = ask_multi("해본 게임 중 좋았던 것", fav_ids, [by_id[x]["name"] for x in fav_ids])
    likes = {
        "view": ask_multi("선호하는 그래픽", unique_values(games, "view")),
        "mood": ask_multi("선호하는 분위기", unique_values(games, "mood")),
        "play": ask_multi("선호하는 플레이 방식", unique_values(games, "play")),
    }
    dislikes = ask_multi("이건 절대 싫다 (가진 게임은 아예 제외)", DISLIKE_OPTIONS,
                         [d.split(":")[1] for d in DISLIKE_OPTIONS])

    survey = {"genres": genres, "subgenres": subgenres, "likes": likes,
              "dislikes": dislikes, "favorites": favorites}
    print("\n설문 결과:", survey)
    return survey


def print_feed(results):
    print("\n" + "-" * 50)
    for i, r in enumerate(results, 1):
        g = r["game"]
        tag = "🔍탐색" if r["slot"] == "탐색" else "  추천"
        indie = " [인디]" if g.get("indie") else ""
        why = ", ".join(r["reasons"]) or "-"
        print(f" {i}. {tag} {g['name']}{indie}  [{g['genre']}/{'·'.join(g['subgenre'])}]  "
              f"부합도 {r['match']}%  (이유: {why})")
    print("-" * 50)
    print(" 명령: l 번호=관심  s 번호=넘김  c 번호=스토어이동  p=내 취향 가중치  q=종료")


def main():
    games = load_games()
    by_id = {g["id"]: g for g in games}
    survey = run_survey(games)
    profile = build_profile(survey, by_id)

    actions = {"l": "like", "s": "skip", "c": "click"}
    while True:
        results = recommend(games, profile, k=FEED_SIZE)
        if not results:
            print("\n더 보여줄 게임이 없습니다. (필터가 너무 좁거나 다 봤음)")
            break
        print_feed(results)
        cmd = input(" > ").strip().split()
        if not cmd:
            continue
        if cmd[0] == "q":
            break
        if cmd[0] == "p":
            print("\n[내 취향 가중치 상위]")
            for f, w in profile.top_weights(15):
                print(f"   {f:<22} {w:+.2f}")
            print("[제외 태그]", profile.excluded or "-")
            continue
        if cmd[0] in actions and len(cmd) == 2 and cmd[1].isdigit():
            idx = int(cmd[1]) - 1
            if 0 <= idx < len(results):
                game = results[idx]["game"]
                apply_feedback(profile, game, actions[cmd[0]])
                print(f"   → '{game['name']}' {actions[cmd[0]]} 반영")
                continue
        print("   명령을 다시 입력하세요.")


if __name__ == "__main__":
    main()

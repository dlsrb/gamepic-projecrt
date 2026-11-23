"""
추천 엔진 숫자 모음 (PROJECT.md 4.1, 4.11 C3). 코드 안에는 숫자를 직접 쓰지 않고 여기서 가져간다.
프로토타입(gamepick_reco/config.py) 값에서 11/2 확정 값으로 바꿨다.
"""

# 태그 종류별 무게: 숫자가 클수록 "이게 맞아야 진짜 비슷한 게임"
CATEGORY_WEIGHTS = {
    "genre": 3.0,     # 대분류 장르 (RPG, 슈팅, 퍼즐 ...)
    "subgenre": 3.0,  # 세부 장르 (턴제RPG, 액션RPG, MMORPG ...)
    "view": 2.0,      # 시점·그래픽 (2D, 3D, 픽셀아트, 횡스크롤 ...)
    "play": 1.0,      # 플레이 방식 (싱글, 멀티, 짧은플레이 ...)
    "mood": 1.0,      # 분위기 (다크판타지, 힐링 ...)
    "bm": 0.5,        # 과금 구조 (유료구매, 가챠 ...)
}

# 태그 점수는 0에서 시작해 이 범위 안에서만 움직인다 (넘으면 잘라냄)
INITIAL_SCORE = 0.0
WEIGHT_MIN, WEIGHT_MAX = -5.0, 5.0

# 설문 시작 점수
SURVEY_WEIGHT = 2.0     # 설문에서 고른 선호 태그
FAVORITE_WEIGHT = 1.0   # "좋아하는 게임"의 태그 (여러 게임에서 겹치면 합산)
DISLIKE_WEIGHT = -5.0   # 비선호 태그. 이 점수(=WEIGHT_MIN)인 태그를 가진 게임은 후보에서 제외

# 반응이 태그 점수를 바꾸는 양
FEEDBACK_DELTA = {
    "like": 1.0,      # 👍 따봉 (게임당 1회, 취소하면 같은 양을 되돌림)
    "store": 0.5,     # 스토어 이동 (게임당 하루 1회만 반영)
    "dislike": -1.0,  # 관심 없음
}

# 추천 목록 중 탐색 슬롯(인디·신작 노출) 비율
EXPLORE_RATIO = 0.2
# 탐색 칸은 조건을 통과한 후보 중 점수 상위 이 수 안에서 무작위로 고른다 (C12-a: 매번 같은 게임이 뜨지 않게)
EXPLORE_POOL = 15

# 화면에 보이는 취향 % (C7-e). 추천 순서(score, 코사인)와는 따로 계산한다
#   취향 % = 100 × (MATCH_COVERAGE_RATIO × 게임 쪽 일치율 + MATCH_RELATIVE_RATIO × 내 1등 게임 대비), 최대 MATCH_MAX
#   게임 쪽 일치율 = (내가 좋아하는 태그 무게 − 싫어하는 태그 무게 × MATCH_NEGATIVE_PENALTY) ÷ 게임 태그 무게 전체
MATCH_COVERAGE_RATIO = 0.7
MATCH_RELATIVE_RATIO = 0.3
MATCH_NEGATIVE_PENALTY = 0.5
MATCH_MAX = 99
# 태그가 이보다 적은 게임은 일치율을 (태그 수 ÷ 이 값)만큼 깎는다 (C7-f). 태그 2개면 일치율 최대 50%
MATCH_MIN_TAGS = 4
# 홈 슬라이드의 기준(min_match) 통과 칸에는 태그가 이보다 적은 게임을 넣지 않는다 (C8-a). 대체 동작·탐색 칸은 상관없음
HOME_MIN_TAGS = 4

# 상세 "비슷한 게임" 순서 = 이 게임과의 유사도 % × 앞 + 내 취향 % × 뒤 (C7-e)
SIMILAR_BLEND_RATIO = (0.6, 0.4)

# ---------- 인기 랭킹·자동 배지·이달의 게임 (C12-c·d·e) ----------
# 인기 랭킹 장르 탭 (전체·Google Play·App Store 다음에 이 순서로). 장르 이름은 태그표의 장르
RANKING_GENRE_TABS = ("RPG", "시뮬레이션", "액션")
RANKING_SIZE = 50                 # 인기 랭킹 탭마다 보여 주는 수

# 자동 배지 (태그와 별개, 검색어 "신작"·"인디"·"인기"·"급상승"으로도 찾음)
NEW_BADGE_DAYS = 30               # 신작: 출시 30일 이내 (오늘 이후 출시 예정은 아님)
INDIE_MAX_INSTALLS = 100_000      # 인디: Google Play 설치 수 10만 이하. 설치 수 없는 게임(iOS만)은 is_indie 값
POPULAR_BADGE_TOP = 30            # 인기: 인기 랭킹 '전체' 1~30위

# 인기 급상승: 점수 = Σ 무게 × 항목(0~1), 최근 SURGE_WINDOW_DAYS일 사이 변화
#   rating = 별점 수 증가율 (증가 ÷ max(기준 별점 수, SURGE_RATING_FLOOR)), 1에서 자름
#   rank   = 차트 순위 상승 (두 스토어 중 높은 순위 기준, 오른 칸 수 ÷ 100. 새로 차트에 들면 101 − 지금 순위)
#   installs = Play 설치 수 구간이 올라갔으면 1 (10만+ → 100만+ 같은 구간 값이라 그 밖엔 판단 안 함)
#   search = 게임픽 검색어 중 이 게임 이름에 들어맞는 횟수 ÷ SURGE_SEARCH_CAP, 1에서 자름
#   engage = 상세 조회·따봉을 남긴 사람 수 ÷ SURGE_ENGAGE_CAP, 1에서 자름
SURGE_WINDOW_DAYS = 7
SURGE_MIN_DAYS = 7                # 가장 오래된 스냅샷이 이보다 최근이면 "기록 쌓는 중" → 배지 없음
SURGE_WEIGHTS = {"rating": 0.35, "rank": 0.30, "installs": 0.10, "search": 0.15, "engage": 0.10}
SURGE_RATING_FLOOR = 50
SURGE_SEARCH_CAP = 10
SURGE_ENGAGE_CAP = 10
SURGE_MIN_SCORE = 0.15            # 이 점수 이상이면서
SURGE_TOP = 10                    # 점수 상위 이 수 안이면 배지

# 이달의 게임 (홈): 이번 달 인기(스냅샷 평균 순위) + 급상승 위주, 취향 %는 약하게
MONTHLY_SIZE = 5
MONTHLY_POOL = 30                 # 이번 달 인기 상위 이 수 + 급상승 배지 게임이 후보
MONTHLY_WEIGHTS = {"popular": 0.6, "surge": 0.3, "match": 0.1}
MONTHLY_SKIP_POPULAR_TOP = 5      # 홈 바로 위 "인기 랭킹" 상위 5개와 겹치지 않게 그 게임은 뺌

# 맞춤 추천 후보 채우기 (C12-i): 태그 검수된 게임 중 후보(비선호·이미 본 게임 거른 뒤)가 이보다 적으면
# 미검수 게임으로 이 수까지 채운다. 채우는 순서 = 점수대(0.1 단위) 높은 순 → 태그 수 많은 순 → 인기 랭킹 높은 순
CHECKED_MIN_CANDIDATES = 9
FILL_SCORE_STEP = 0.1

# 인기 랭킹 탭 채우기 (C12-k): 차트 순위 게임이 이보다 적은 탭(장르·스토어)은 차트 밖 게임을 이어서 채움
RANKING_MIN_ROWS = 20
# 차트 밖 순서 점수 = Σ 무게 × log10(값 + 1) ÷ log10(탭 안 최댓값 + 1)  (값: 설치 수, 별점 수, 게임픽 따봉 수)
# 설치 수가 없는 iOS 전용 게임은 설치 수 몫이 0 → 별점 수·따봉으로만
OFFCHART_WEIGHTS = {"installs": 0.5, "ratings": 0.4, "likes": 0.1}

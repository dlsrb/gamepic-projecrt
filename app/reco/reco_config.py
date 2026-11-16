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

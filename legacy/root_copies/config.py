"""
추천 엔진 설정값. 실험할 때 주로 이 파일의 숫자를 바꿔 보면 된다.
"""
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent / "data" / "games.json"

# 태그 종류별 무게: 숫자가 클수록 "이게 맞아야 진짜 비슷한 게임"
CATEGORY_WEIGHTS = {
    "genre": 3.0,     # 대분류 장르 (RPG, 슈팅, 퍼즐 ...)
    "subgenre": 3.0,  # 세부 장르 (턴제RPG, 액션RPG, MMORPG ...)
    "view": 2.0,      # 시점·그래픽 (2D, 3D, 픽셀아트, 횡스크롤 ...)
    "play": 1.0,      # 플레이 방식 (싱글, 멀티, 짧은플레이 ...)
    "mood": 1.0,      # 분위기 (다크판타지, 힐링 ...)
    "bm": 0.5,        # 과금 구조 (유료구매, 가챠 ...)
}

# 설문에서 선택한 항목이 받는 초기 가중치
SURVEY_WEIGHT = 1.0
FAVORITE_WEIGHT = 1.0   # "좋아하는 게임"으로 고른 게임의 태그 전체에 더하는 값

# 피드 반응이 가중치를 바꾸는 양
FEEDBACK_DELTA = {
    "like": 0.5,    # 관심 있어요
    "skip": -0.2,   # 넘김
    "click": 1.0,   # 스토어 이동 (가장 강한 신호)
}

# 가중치가 한쪽으로 폭주하지 않게 막는 범위
WEIGHT_MIN, WEIGHT_MAX = -2.0, 3.0

# 추천 목록 중 탐색 슬롯(인디·신작 노출) 비율
EXPLORE_RATIO = 0.2

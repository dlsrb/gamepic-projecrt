import os
import re
import time
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import pandas as pd
import requests
from google_play_scraper import Sort, app, reviews, search

# ==========================================
# 1. 설정 및 파일 경로
# ==========================================
GAME_LIST_PATH = "game_list.csv"
HINTS_PATH = "game_id_hints.csv"

OUT_GAME_LIST = "game_list.csv"
OUT_CHECK_LIST = "game_list_check.csv"

LOG_DIR = "logs"
FAILED_LOG_PATH = os.path.join(LOG_DIR, "id_fill_failed.txt")

# 제외할 단어 목록 (소문자 기준)
EXCLUDE_KEYWORDS = [
    "가이드",
    "공략",
    "도우미",
    "companion",
    "guide",
    "wiki",
    "tips",
]

# 12개월 전 날짜
CUTOFF_DATE = datetime.now() - timedelta(days=365)


def log_message(msg):
    """콘솔 출력 및 로그 파일 실시간 기록"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_msg = f"[{timestamp}] {msg}"
    print(formatted_msg)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(FAILED_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(formatted_msg + "\n")


def contains_excluded_keyword(text):
    """도우미/공략/가이드 키워드 포함 여부 검사"""
    if not text:
        return False
    text_lower = str(text).lower()
    return any(keyword in text_lower for keyword in EXCLUDE_KEYWORDS)


def parse_installs_count(installs_str):
    if not installs_str:
        return 0
    num_str = re.sub(r"[^\d]", "", str(installs_str))
    return int(num_str) if num_str else 0


# ==========================================
# 2. 고급 텍스트 유사도 계산
# ==========================================
def get_title_variations(text):
    """제목을 여러 버전(전체, 괄호 안, 괄호 밖, 구분자 앞)으로 분해하여 반환"""
    if not text or pd.isna(text):
        return []
    text = str(text).lower()
    text = text.replace("®", "").replace("™", "")

    variations = set()

    def clean_and_add(t):
        # 영문, 숫자, 한글 이외의 특수문자/공백 제거
        t_clean = re.sub(r"[^\w\s가-힣a-z0-9]", "", t).replace(" ", "")
        if t_clean:
            variations.add(t_clean)

    # 1. 전체 텍스트
    clean_and_add(text)

    # 2. 괄호 안/밖 분리
    if "(" in text and ")" in text:
        inside = re.findall(r"\((.*?)\)", text)
        for i in inside:
            clean_and_add(i)
        outside = re.sub(r"\(.*?\)", "", text)
        clean_and_add(outside)

    # 3. 특정 구분자(:, -) 앞부분
    for sep in [":", "-"]:
        if sep in text:
            clean_and_add(text.split(sep)[0])

    return list(variations)


def calculate_advanced_similarity(name, name_en, target_title):
    """
    name과 name_en 조합 중 target_title(앱 제목)과 가장 높은 유사도 반환
    한쪽이 다른 쪽에 통째로 포함되면 최소 0.9 적용
    """
    q_vars = get_title_variations(name) + get_title_variations(name_en)
    t_vars = get_title_variations(target_title)

    if not q_vars or not t_vars:
        return 0.0

    max_score = 0.0
    for q in q_vars:
        for t in t_vars:
            sim = SequenceMatcher(None, q, t).ratio()
            # 한쪽이 통째로 들어있는 경우 0.9로 상향 처리
            if q in t or t in q:
                sim = max(0.9, sim)

            if sim > max_score:
                max_score = sim

    return max_score


# ==========================================
# 3. Android 매칭 함수
# ==========================================
def process_android(game_name, name_en, android_hint):
    # 1. 힌트 처리: 있으면 상세 조회 후 GAME_ 장르 시 무조건 채택
    if pd.notna(android_hint) and str(android_hint).strip():
        hint_id = str(android_hint).strip()
        try:
            detail = app(hint_id, lang="ko", country="kr")
            genre_id = str(detail.get("genreId", "")).upper()
            title = detail.get("title", "")

            # 장르가 게임인 경우 유사도 검사 생략하고 무조건 채택
            if genre_id.startswith("GAME"):
                return (
                    hint_id,
                    title,
                    detail.get("developer", ""),
                    1.0,  # 무조건 채택이므로 임의로 1.0(Max) 부여
                    detail,
                    "Hint 조건 충족 (무조건 채택)",
                )
            else:
                log_message(
                    f"[Android Hint 거절] {game_name} -> hint '{hint_id}': 게임 장르 아님 (genre: {genre_id})"
                )
        except Exception as e:
            log_message(
                f"[Android Hint 조회 실패] {game_name} -> hint '{hint_id}': {e}"
            )

    # 2. 힌트가 없거나 실패한 경우 검색 기반 매칭
    try:
        # name과 name_en 각각 검색 후 결과 병합 (중복 제거)
        raw_results = []
        try:
            res1 = search(game_name, lang="ko", country="kr")
            if res1: raw_results.extend(res1)
        except:
            pass

        if name_en:
            try:
                res2 = search(name_en, lang="ko", country="kr")
                if res2: raw_results.extend(res2)
            except:
                pass

        if not raw_results:
            return None, "", "", 0.0, None, "검색 결과 없음"

        candidates = []
        seen_ids = set()

        for item in raw_results:
            # NoneType 에러 방어
            if not isinstance(item, dict):
                continue

            app_id = item.get("appId")
            title = item.get("title", "")

            if not app_id or app_id in seen_ids:
                continue
            seen_ids.add(app_id)

            if contains_excluded_keyword(title):
                continue

            sim = calculate_advanced_similarity(game_name, name_en, title)
            candidates.append((sim, app_id, title, item))

        candidates.sort(key=lambda x: x[0], reverse=True)

        valid_candidates = []
        best_match = None

        # 상위 10개만 검사 (과부하 방지)
        for sim, app_id, title, item in candidates[:10]:
            try:
                detail = app(app_id, lang="ko", country="kr")
                genre_id = str(detail.get("genreId", "")).upper()
                if genre_id.startswith("GAME"):
                    valid_candidates.append((sim, app_id, title, detail))
                    if best_match is None:
                        best_match = (sim, app_id, title, detail)
            except Exception:
                continue

        if not best_match:
            return None, "", "", 0.0, None, "유효한 게임 카테고리 앱 없음"

        best_sim, best_id, best_title, best_detail = best_match

        if best_sim < 0.6:
            cand_info = [
                f"{t}(점수: {s:.2f})" for s, _, t, _ in valid_candidates[:3]
            ]
            note_msg = f"유사도 낮음 (<0.6): {', '.join(cand_info)}"
            log_message(f"[Android 유사도 미달] {game_name} -> {note_msg}")
            return None, "", "", round(best_sim, 3), None, note_msg

        return (
            best_id,
            best_title,
            best_detail.get("developer", ""),
            round(best_sim, 3),
            best_detail,
            "정상 매칭",
        )
    except Exception as e:
        log_message(f"[Android 검색 예외] {game_name}: {e}")
        return None, "", "", 0.0, None, f"오류 발생: {e}"


# ==========================================
# 4. iOS 매칭 함수
# ==========================================
def fetch_ios_search(query):
    if not query: return []
    try:
        url = "https://itunes.apple.com/search"
        params = {"term": query, "country": "kr", "entity": "software"}
        res = requests.get(url, params=params, timeout=10)
        return res.json().get("results", []) if res.status_code == 200 else []
    except:
        return []


def process_ios(game_name, name_en):
    try:
        raw_results = fetch_ios_search(game_name)
        if name_en:
            raw_results.extend(fetch_ios_search(name_en))

        if not raw_results:
            return None, "", "검색 결과 없음"

        candidates = []
        seen_ids = set()

        for item in raw_results:
            # NoneType 에러 방어
            if not isinstance(item, dict):
                continue

            title = item.get("trackName", "")
            track_id = str(item.get("trackId", ""))

            if not track_id or track_id == "None" or track_id in seen_ids:
                continue
            seen_ids.add(track_id)

            if contains_excluded_keyword(title):
                continue

            genre_ids = [str(g) for g in item.get("genreIds", [])]
            primary_genre_id = str(item.get("primaryGenreId", ""))
            primary_genre_name = str(item.get("primaryGenreName", ""))

            is_game = (
                    "6014" in genre_ids
                    or primary_genre_id == "6014"
                    or "Game" in primary_genre_name
                    or "게임" in primary_genre_name
            )

            if not is_game:
                continue

            sim = calculate_advanced_similarity(game_name, name_en, title)
            candidates.append((sim, track_id, title))

        if not candidates:
            return None, "", "유효한 게임 카테고리 앱 없음"

        candidates.sort(key=lambda x: x[0], reverse=True)
        best_sim, best_id, best_title = candidates[0]

        if best_sim < 0.6:
            cand_info = [
                f"{t}(점수: {s:.2f})" for s, _, t in candidates[:3]
            ]
            note_msg = f"iOS 유사도 낮음 (<0.6): {', '.join(cand_info)}"
            log_message(f"[iOS 유사도 미달] {game_name} -> {note_msg}")
            return None, "", note_msg

        return best_id, best_title, "정상 매칭"

    except Exception as e:
        log_message(f"[iOS 검색 예외] {game_name}: {e}")
        return None, "", f"오류 발생: {e}"


# ==========================================
# 5. 리뷰 개수 및 메인 함수
# ==========================================
def fetch_android_review_stats(android_id):
    if not android_id:
        return "", 0
    try:
        recent_reviews, _ = reviews(
            android_id, lang="ko", country="kr", sort=Sort.NEWEST, count=200
        )
        reviews_12m_count = 0
        last_date = None
        if recent_reviews:
            last_date = recent_reviews[0].get("at")
            for r in recent_reviews:
                review_at = r.get("at")
                if review_at and review_at >= CUTOFF_DATE:
                    reviews_12m_count += 1
        return last_date.strftime("%Y-%m-%d") if last_date else "", reviews_12m_count
    except Exception:
        return "", 0


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    log_message("=== 모바일 게임 리뷰 크롤러 ID 매칭 작업 시작 ===")

    if not os.path.exists(GAME_LIST_PATH):
        log_message(f"[오류] '{GAME_LIST_PATH}' 파일이 존재하지 않습니다.")
        return

    df_games = pd.read_csv(
        GAME_LIST_PATH, encoding="utf-8", dtype={"android_id": str, "ios_id": str}
    )
    df_games["android_id"] = df_games["android_id"].fillna("")
    df_games["ios_id"] = df_games["ios_id"].fillna("")

    # name_en 컬럼이 없을 경우 빈 문자열 처리
    if "name_en" not in df_games.columns:
        df_games["name_en"] = ""

    if os.path.exists(HINTS_PATH):
        df_hints = pd.read_csv(HINTS_PATH, encoding="utf-8", dtype=str)
        if "android_hint" in df_hints.columns:
            df_games = pd.merge(
                df_games,
                df_hints[["game_id", "android_hint"]],
                on="game_id",
                how="left",
            )
        else:
            df_games["android_hint"] = ""
    else:
        df_games["android_hint"] = ""

    check_records = []

    print(f">>> 총 {len(df_games)}개 게임 대상 매칭 탐색 중...\n")

    for idx, row in df_games.iterrows():
        game_id = str(row["game_id"]).strip()
        name = str(row["name"]).strip()
        name_en = str(row["name_en"]).strip() if pd.notna(row.get("name_en")) else ""
        tier = str(row.get("tier", "")).strip()
        android_hint = row.get("android_hint", "")

        print(f"[{idx + 1}/{len(df_games)}] '{name}' (game_id: {game_id}) 수집 시작...")

        # 1. Android
        a_id, a_title, a_dev, a_score, a_detail, a_note = process_android(
            name, name_en, android_hint
        )
        time.sleep(1.2)

        # 2. iOS
        i_id, i_title, i_note = process_ios(name, name_en)
        time.sleep(1.2)

        if a_detail:
            installs_str = a_detail.get("installs", "0+")
            installs_num = parse_installs_count(installs_str)
            score = round(a_detail.get("score") or 0.0, 2)
            ratings = a_detail.get("ratings") or 0
        else:
            installs_str, installs_num, score, ratings = "0", 0, 0.0, 0

        last_review_date, recent_reviews_12m = fetch_android_review_stats(a_id)

        suggested_tier = (
            "대형" if installs_num >= 10000000
            else "중견" if installs_num >= 1000000
            else "인디"
        )

        status = "정상"
        notes = []
        if not a_id and not i_id:
            status = "스토어에서 못 찾음"
            notes.append("양쪽 스토어 찾지 못함")
        elif recent_reviews_12m < 5 and last_review_date:
            status = "최근 리뷰 거의 없음"
            notes.append("최근 12개월 리뷰 건수 저조")

        if a_note and "정상" not in a_note and "Hint" not in a_note:
            notes.append(f"Android: {a_note}")
        if i_note and "정상" not in i_note:
            notes.append(f"iOS: {i_note}")

        check_records.append(
            {
                "game_id": game_id,
                "name": name,
                "tier": tier,
                "suggested_tier": suggested_tier,
                "installs": installs_str,
                "score": score,
                "ratings": ratings,
                "last_review_date": last_review_date,
                "recent_reviews_12m": recent_reviews_12m,
                "android_found": bool(a_id),
                "ios_found": bool(i_id),
                "android_id": a_id or "",
                "ios_id": i_id or "",
                "android_title": a_title,
                "android_developer": a_dev,
                "match_score": a_score,
                "ios_title": i_title,
                "status": status,
                "note": " / ".join(notes) if notes else ("Hint 적용" if "Hint" in a_note else ""),
            }
        )

    df_check = pd.DataFrame(check_records)

    # 6. 중복 ID 검출 및 무효화 (3번 규칙)
    for platform in ["android_id", "ios_id"]:
        valid_ids = df_check[df_check[platform] != ""][platform]
        duplicate_ids = valid_ids[valid_ids.duplicated()].unique()

        for dup_id in duplicate_ids:
            dup_indices = df_check[df_check[platform] == dup_id].index
            dup_names = df_check.loc[dup_indices, "name"].tolist()
            dup_msg = f"중복 ID 발견({dup_id}) - 게임: {', '.join(dup_names)}"

            log_message(f"[{platform.upper()} 중복 ID 처리] {dup_msg}")

            for dup_idx in dup_indices:
                df_check.loc[dup_idx, platform] = ""
                df_check.loc[dup_idx, platform.replace("_id", "_found")] = False
                prev_note = df_check.loc[dup_idx, "note"]
                df_check.loc[dup_idx, "status"] = "중복 ID 제거됨"
                df_check.loc[dup_idx, "note"] = f"{prev_note} / {dup_msg}".strip(" / ")

    # 7. 파일 저장
    df_output_list = df_games[["game_id", "name", "tier", "genre"]].copy()
    if "name_en" in df_games.columns:
        df_output_list.insert(2, "name_en", df_games["name_en"])  # 기존 컬럼 구조 유지 고려

    df_output_list["android_id"] = df_check["android_id"]
    df_output_list["ios_id"] = df_check["ios_id"]
    df_output_list.to_csv(OUT_GAME_LIST, index=False, encoding="utf-8-sig")

    check_columns = [
        "game_id", "name", "tier", "suggested_tier", "installs", "score", "ratings",
        "last_review_date", "recent_reviews_12m", "android_found", "ios_found",
        "android_title", "android_developer", "match_score", "ios_title", "status", "note"
    ]
    df_check_final = df_check[check_columns]
    df_check_final.to_csv(OUT_CHECK_LIST, index=False, encoding="utf-8-sig")

    log_message("=== 작업 완료 ===")
    print("\n" + "=" * 50 + "\n완료되었습니다. 결과 파일 및 로그를 확인해 주세요.\n" + "=" * 50)


if __name__ == "__main__":
    main()
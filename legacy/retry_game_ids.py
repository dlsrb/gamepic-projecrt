import os
import re
import time
from datetime import datetime
from difflib import SequenceMatcher
import pandas as pd
from google_play_scraper import app, search

# ==========================================
# 1. 설정 및 파일 경로
# ==========================================
RETRY_LIST_PATH = "game_id_retry.csv"
MAIN_LIST_PATH = "game_list.csv"
OUT_RETRY_RESULT = "game_id_retry_result.csv"
LOG_DIR = "logs"
FAILED_LOG_PATH = os.path.join(LOG_DIR, "retry_failed.txt")

# 제외할 단어 목록 (소문자 기준 변환)
EXCLUDE_KEYWORDS = [
    "가이드", "공략", "도우미", "companion", "guide", "wiki", "tips",
    "체험판", "demo", "lite", "넷플릭스", "netflix"
]


def log_message(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_msg = f"[{timestamp}] {msg}"
    print(formatted_msg)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(FAILED_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(formatted_msg + "\n")


def contains_excluded_keyword(text):
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
# 2. 고급 텍스트 유사도 계산 (이전과 동일)
# ==========================================
def get_title_variations(text):
    if not text or pd.isna(text):
        return []
    text = str(text).lower()
    text = text.replace("®", "").replace("™", "")

    variations = set()

    def clean_and_add(t):
        t_clean = re.sub(r"[^\w\s가-힣a-z0-9]", "", t).replace(" ", "")
        if t_clean:
            variations.add(t_clean)

    clean_and_add(text)
    if "(" in text and ")" in text:
        inside = re.findall(r"\((.*?)\)", text)
        for i in inside: clean_and_add(i)
        clean_and_add(re.sub(r"\(.*?\)", "", text))
    for sep in [":", "-"]:
        if sep in text:
            clean_and_add(text.split(sep)[0])
    return list(variations)


def calculate_advanced_similarity(name, name_en, target_title):
    q_vars = get_title_variations(name) + get_title_variations(name_en)
    t_vars = get_title_variations(target_title)
    if not q_vars or not t_vars: return 0.0

    max_score = 0.0
    for q in q_vars:
        for t in t_vars:
            sim = SequenceMatcher(None, q, t).ratio()
            if q in t or t in q:
                sim = max(0.9, sim)
            if sim > max_score:
                max_score = sim
    return max_score


# ==========================================
# 3. Android Retry 매칭 함수
# ==========================================
def process_retry(game_name, name_en, candidates_str):
    """
    1. candidates 파이프(|) 구분자로 확인
    2. 실패시 search(name_en) fallback
    """
    candidates = [c.strip() for c in str(candidates_str).split("|") if c.strip()]

    # 1. 후보 패키지 확인
    for cand_id in candidates:
        try:
            detail = app(cand_id, lang="ko", country="kr")
            title = detail.get("title", "")
            genre_id = str(detail.get("genreId", "")).upper()

            if not genre_id.startswith("GAME") or contains_excluded_keyword(title):
                continue

            sim = calculate_advanced_similarity(game_name, name_en, title)
            if sim >= 0.6:
                return (
                    cand_id, title, detail.get("developer", ""),
                    round(sim, 3), detail, f"후보 목록에서 채택 ({cand_id})"
                )
            else:
                log_message(f"[후보 거절] {game_name} -> {cand_id} (유사도 미달: {sim:.2f})")
        except Exception:
            # 앱이 존재하지 않거나 오류 발생 시 다음 후보로
            continue

    # 2. 후보가 다 틀렸을 경우 search()로 재검색
    try:
        raw_results = []
        if name_en:
            res_en = search(name_en, lang="ko", country="kr")
            if res_en: raw_results.extend(res_en)

        res_ko = search(game_name, lang="ko", country="kr")
        if res_ko: raw_results.extend(res_ko)

        if not raw_results:
            return None, "", "", 0.0, None, "검색 결과 없음"

        search_cands = []
        seen_ids = set()

        for item in raw_results:
            if not isinstance(item, dict): continue

            app_id = item.get("appId")
            title = item.get("title", "")
            if not app_id or app_id in seen_ids or contains_excluded_keyword(title):
                continue

            seen_ids.add(app_id)
            sim = calculate_advanced_similarity(game_name, name_en, title)
            search_cands.append((sim, app_id, title, item))

        search_cands.sort(key=lambda x: x[0], reverse=True)
        valid_candidates = []
        best_match = None

        for sim, app_id, title, item in search_cands[:10]:
            try:
                detail = app(app_id, lang="ko", country="kr")
                if str(detail.get("genreId", "")).upper().startswith("GAME"):
                    valid_candidates.append((sim, app_id, title, detail))
                    if best_match is None:
                        best_match = (sim, app_id, title, detail)
            except:
                continue

        if not best_match:
            return None, "", "", 0.0, None, "유효한 게임 없음"

        best_sim, best_id, best_title, best_detail = best_match

        if best_sim < 0.6:
            cand_info = [f"{t}({aid}, 점수:{s:.2f})" for s, aid, t, _ in valid_candidates[:3]]
            note_msg = f"유사도 미달 (<0.6): {', '.join(cand_info)}"
            log_message(f"[재검색 유사도 미달] {game_name} -> {note_msg}")
            return None, "", "", round(best_sim, 3), None, note_msg

        return (
            best_id, best_title, best_detail.get("developer", ""),
            round(best_sim, 3), best_detail, "search(name_en)으로 채택"
        )

    except Exception as e:
        log_message(f"[재검색 오류] {game_name}: {e}")
        return None, "", "", 0.0, None, f"검색 오류: {e}"


# ==========================================
# 4. 메인 실행 함수
# ==========================================
def main():
    if not os.path.exists(RETRY_LIST_PATH) or not os.path.exists(MAIN_LIST_PATH):
        print("[오류] 필요한 CSV 파일(game_id_retry.csv 또는 game_list.csv)이 존재하지 않습니다.")
        return

    df_retry = pd.read_csv(RETRY_LIST_PATH, encoding="utf-8")
    df_main = pd.read_csv(MAIN_LIST_PATH, encoding="utf-8")

    # name_en이 없는 경우 처리
    if "name_en" not in df_retry.columns: df_retry["name_en"] = ""

    result_records = []
    found_updates = {}

    print(f">>> 총 {len(df_retry)}개 누락/실패 게임 재탐색 시작...\n")

    for idx, row in df_retry.iterrows():
        game_id = str(row["game_id"]).strip()
        name = str(row["name"]).strip()
        name_en = str(row["name_en"]).strip() if pd.notna(row.get("name_en")) else ""
        candidates_str = row.get("android_candidates", "")

        print(f"[{idx + 1}/{len(df_retry)}] '{name}' 탐색 중...")

        a_id, a_title, a_dev, a_score, a_detail, a_note = process_retry(
            name, name_en, candidates_str
        )
        time.sleep(1)  # API 과부하 방지

        if a_detail:
            installs_str = a_detail.get("installs", "0+")
            score = round(a_detail.get("score") or 0.0, 2)
            ratings = a_detail.get("ratings") or 0
        else:
            installs_str, score, ratings = "0", 0.0, 0

        result_records.append({
            "game_id": game_id,
            "name": name,
            "android_id": a_id or "",
            "android_title": a_title,
            "android_developer": a_dev,
            "installs": installs_str,
            "score": score,
            "ratings": ratings,
            "match_score": a_score,
            "note": a_note
        })

        # 0.6 이상 채택된 경우에만 덮어쓰기용 Dictionary에 저장
        if a_id:
            found_updates[game_id] = a_id

    # 1. 재탐색 결과 별도 파일 저장
    df_result = pd.DataFrame(result_records)
    df_result.to_csv(OUT_RETRY_RESULT, index=False, encoding="utf-8-sig")

    # 2. 메인 리스트 (game_list.csv) 덮어쓰기 로직
    df_main["android_id"] = df_main["android_id"].astype(str).replace("nan", "")

    updated_count = 0
    for idx, row in df_main.iterrows():
        g_id = str(row["game_id"]).strip()
        if g_id in found_updates:
            df_main.at[idx, "android_id"] = found_updates[g_id]
            updated_count += 1

    df_main.to_csv(MAIN_LIST_PATH, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 50)
    print(f"재탐색 완료! (총 {len(df_retry)}개 중 {updated_count}개 추가 채택)")
    print(f"- 상세 결과 저장: {OUT_RETRY_RESULT}")
    print(f"- 메인 파일({MAIN_LIST_PATH}) 업데이트 완료")
    print("=" * 50)


if __name__ == "__main__":
    main()
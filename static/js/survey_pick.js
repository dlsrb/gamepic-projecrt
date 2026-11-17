// 설문 보기 줄이기의 계산 부분 (C11-f). 화면 코드와 떼어 tests/test_c11.py가 node로 바로 확인한다.

const normalize = (text) => String(text || "").toLowerCase().replace(/\s+/g, "");

/** 보기가 검색어에 맞는지: 이름 또는 검색 동의어(aliases) 부분 일치, 띄어쓰기·대소문자 무시 */
export function optionMatches(option, query) {
  const needle = normalize(query);
  if (!needle) return false;
  return [option.label, ...(option.aliases || [])].some((word) => normalize(word).includes(needle));
}

/**
 * 5번 대표작: 3번에서 고른 태그('종류:태그')와 겹치는 수(부합도) → 추천 수 → 설치 수 → 원래 순서로 n개.
 * 3번 답이 없거나 아무것도 안 겹치면 추천 수·설치 수 순 (많이 알려진 게임)
 */
export function featuredGames(options, picked, n = 3) {
  const liked = new Set(picked);
  const overlap = (o) => (o.features || []).filter((f) => liked.has(f)).length;
  return options
    .map((o, i) => ({ o, i, score: overlap(o) }))
    .sort((a, b) => b.score - a.score || (b.o.likes || 0) - (a.o.likes || 0)
      || (b.o.installs || 0) - (a.o.installs || 0) || a.i - b.i)
    .slice(0, n)
    .map((x) => x.o);
}

/** 검색어가 비었는지 (공백만이면 빈 것) */
export const isBlank = (query) => !normalize(query);

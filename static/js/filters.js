// 신작 필터 판정 (C11-d). 화면 코드와 떼어 tests/test_c11.py가 node로 바로 확인한다.
// 장르는 하나라도 맞으면(OR), 키워드는 고른 것이 모두 있어야(AND), 장르와 키워드는 AND
export function matchesFilter(game, genres, keywords) {
  if (genres.size && !genres.has(game.genre)) return false;
  const tags = new Set(game.keywords || game.tags || []);  // keywords = 화면 칩과 별개인 전체 태그 (C12-a: 미검수 게임은 칩이 장르만)
  return [...keywords].every((k) => tags.has(k));
}

const yearOf = (game) => Number(String(game.release_date || "").slice(0, 4)) || null;

/**
 * 신작 칸 나누기 (C11-h). [[제목, 게임 목록]] — 빈 칸은 뺌, 게임 순서는 받은 그대로.
 * 기본(all=false): 올해작 한 칸만 / 전체 보기(all=true): 연도마다 칸(최신 연도부터) + 맨 끝 "출시일 없음"
 */
export function yearGroups(games, thisYear, all = false) {
  if (!all) return [[`${thisYear}년작`, games.filter((g) => yearOf(g) === thisYear)]].filter(([, list]) => list.length);
  const years = [...new Set(games.map(yearOf).filter(Boolean))].sort((a, b) => b - a);
  return [
    ...years.map((year) => [`${year}년작`, games.filter((g) => yearOf(g) === year)]),
    ["출시일 없음", games.filter((g) => yearOf(g) === null)],
  ].filter(([, list]) => list.length);
}

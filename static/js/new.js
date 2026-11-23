// 신작 (C8-b → C11-d → C11-h)
// 기본: 올해작(2026년작) 한 칸만. 맨 아래 "전체 보기" → 같은 페이지 ?all=1 (history에 남겨 뒤로 가기로 올해 화면 복귀):
// 연도마다 칸(2026 → 2025 → …) + 맨 끝 "출시일 없음". 칸 나누기는 filters.js의 yearGroups
// 필터: "필터" 버튼으로 여는 영역 안에 장르 칸·키워드 칸을 따로. 장르는 하나라도 맞으면(OR), 키워드는 모두 있어야(AND),
// 장르와 키워드는 AND. 고른 필터는 위에 칩(×로 하나씩 끄기) + "초기화". 두 화면 모두 같은 필터. 거르기는 화면에서 (/api/new-games)
import { api, bottomNav, el, emptyState, gameCard, tagChip, topBar } from "/static/js/common.js";
import { matchesFilter, yearGroups } from "/static/js/filters.js";

const PAGE = 18;      // 기본 화면(올해작) 처음 보이는 수 (3열 × 6줄), "더 보기"로 같은 수만큼 더
const PAGE_ALL = 9;   // 전체 보기는 연도마다 3줄만, 나머지는 "더 보기"
const isAll = () => new URLSearchParams(location.search).get("all") === "1";
const KEYWORD_LABELS = { subgenre: "세부 장르", play: "플레이", mood: "분위기", view: "그래픽", bm: "과금" };
const app = document.getElementById("app");

/** "2026-11-07" → 올해면 "11/7 출시", 아니면 "2018. 3. 2. 출시" */
function releaseLabel(date, thisYear) {
  const [y, m, d] = (date || "").split("-").map(Number);
  if (!y || !m || !d) return "출시일 정보 없음";
  return y === thisYear ? `${m}/${d} 출시` : `${y}. ${m}. ${d}. 출시`;
}

function newCard(game, thisYear) {
  // 신작 페이지에선 "신작" 배지가 모든 카드에 붙어 의미가 없어서 뺌 (C12-f)
  const card = gameCard({ ...game, badges: (game.badges || []).filter((b) => b !== "신작") }, { maxTags: 2 });
  card.append(el("span", { class: "game-card__date" }, releaseLabel(game.release_date, thisYear)));
  return card;
}

/** 칸 하나: 제목 + 카드 격자 + "더 보기" */
function yearSection(title, games, thisYear, page) {
  let shown = 0;
  const grid = el("div", { class: "grid-3" });
  const more = el("button", { type: "button", class: "btn btn--ghost btn--block page-more" }, "더 보기");
  const showMore = () => {
    grid.append(...games.slice(shown, shown + page).map((g) => newCard(g, thisYear)));
    shown = Math.min(games.length, shown + page);
    more.hidden = shown >= games.length;
  };
  more.addEventListener("click", showMore);
  showMore();
  return el("section", { class: "section new-year" },
    el("h2", { class: "section__title" }, `${title} `, el("span", { class: "new-year__count" }, `${games.length}개`)),
    grid, more);
}

async function start() {
  const genres = new Set();
  const keywords = new Set();
  const filterButton = el("button", {
    type: "button", class: "btn btn--ghost new-filter__toggle", "aria-expanded": "false", "aria-controls": "new-filter-panel",
  });
  const panel = el("div", { class: "card new-filter__panel", id: "new-filter-panel", hidden: true });
  const picked = el("div", { class: "chips new-filter__picked", "aria-label": "고른 필터" });
  const box = el("div", {}, el("p", { class: "placeholder-text" }, "불러오는 중…"));
  const desc = el("p", { class: "page-desc" });
  const footer = el("div", { class: "new-footer" });

  app.append(
    topBar({ left: "back" }),
    el("h1", { class: "section__title page-title" }, "신작"),
    desc,
    el("div", { class: "new-filter" }, filterButton, picked),
    panel, box, footer);
  bottomNav("new");

  let data;
  try {
    data = await api("/api/new-games");
  } catch (error) {
    box.replaceChildren(emptyState(error.message || "신작을 불러오지 못했어요"));
    return;
  }
  const [thisYear] = data.years;

  // 전체 보기 ↔ 올해작: 주소만 바꾸고(history) 같은 데이터·필터로 다시 그림
  const showAll = () => {
    history.pushState({ newAll: true }, "", "?all=1");
    render();
    window.scrollTo(0, 0);
  };
  const showThisYear = () => {
    if (history.state?.newAll) history.back();          // 올해 화면에서 넘어왔으면 뒤로 가기와 같게
    else {                                               // ?all=1로 바로 들어왔으면 주소만 바꿈
      history.replaceState(null, "", location.pathname);
      render();
    }
    window.scrollTo(0, 0);
  };
  window.addEventListener("popstate", () => render());

  const render = () => {
    const count = genres.size + keywords.size;
    filterButton.textContent = count ? `필터 ${count}` : "필터";
    // 위의 고른 필터 칩 (×로 끄기) + 초기화
    picked.replaceChildren(...[
      ...[...genres].map((g) => tagChip(g, { selected: true, removable: true, onClick: () => { genres.delete(g); render(); } })),
      ...[...keywords].map((k) => tagChip(k, { selected: true, removable: true, onClick: () => { keywords.delete(k); render(); } })),
      count ? el("button", { type: "button", class: "btn btn--ghost new-filter__reset", onclick: () => {
        genres.clear(); keywords.clear(); render();
      } }, "초기화") : null,
    ].filter(Boolean));
    for (const chip of picked.querySelectorAll(".chip")) chip.setAttribute("aria-label", `${chip.textContent} 필터 끄기`);

    // 필터 영역: 장르 칸 / 키워드 칸 (종류별 작은 제목)
    const toggleIn = (set, value) => () => { if (set.has(value)) set.delete(value); else set.add(value); render(); };
    panel.replaceChildren(
      el("h3", { class: "new-filter__title" }, "장르", el("span", { class: "new-filter__note" }, "하나라도 맞으면")),
      el("div", { class: "chips" }, data.filters.genre.map((g) => tagChip(g, { selected: genres.has(g), onClick: toggleIn(genres, g) }))),
      el("h3", { class: "new-filter__title" }, "키워드", el("span", { class: "new-filter__note" }, "고른 것이 모두 있는 게임")),
      ...Object.entries(data.filters.keyword).filter(([, names]) => names.length).map(([category, names]) =>
        el("div", { class: "new-filter__group" },
          el("span", { class: "new-filter__group-label" }, KEYWORD_LABELS[category] || category),
          el("div", { class: "chips" }, names.map((k) => tagChip(k, { selected: keywords.has(k), onClick: toggleIn(keywords, k) }))))));

    // 칸 나누기: 기본 = 올해작만, 전체 보기 = 연도별 + 출시일 없음
    const all = isAll();
    desc.textContent = all ? "출시 연도별 전체 목록이에요" : `${thisYear}년에 나온 게임이에요`;
    footer.replaceChildren(all
      ? el("button", { type: "button", class: "btn btn--ghost btn--block page-more", onclick: showThisYear }, `${thisYear}년작만 보기`)
      : el("button", { type: "button", class: "btn btn--ghost btn--block page-more", onclick: showAll }, "전체 보기 (연도별)"));
    const games = data.games.filter((g) => matchesFilter(g, genres, keywords));
    const groups = yearGroups(games, thisYear, all);
    if (!groups.length) {
      const what = all ? "신작" : `${thisYear}년작`;
      box.replaceChildren(emptyState(count ? `조건에 맞는 ${what}이 없어요` : `보여 줄 ${what}이 아직 없어요`));
      return;
    }
    box.replaceChildren(...groups.map(([title, list]) => yearSection(title, list, thisYear, all ? PAGE_ALL : PAGE)));
  };

  filterButton.addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    filterButton.setAttribute("aria-expanded", String(!panel.hidden));
  });
  render();
}

start();

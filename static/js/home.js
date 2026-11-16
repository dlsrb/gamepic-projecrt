// 홈 (4.4 ②, 4.4.1 와이어프레임 02, C8-b 정리)
// 상단바(🌙 / 프로필) → 검색창 → 설문 맞춤 추천(슬라이드) → 추천 순위 상위 5개 + 전체 보기 → 최근 본 게임 / 하단 메뉴
// 인기·신작 탭은 C8-b에서 랭킹·신작 페이지로 옮김
import {
  api, bottomNav, el, emptyState, gameSlider, matchBadge, pages, rankList, thumbnail, topBar,
} from "/static/js/common.js";

const MATCH_LIMIT = 9, MIN_MATCH = 60;  // C8-a: 70 → 60 (서버가 태그 3개 이하 게임은 기준 통과 칸에서 뺌)
const RANK_PREVIEW = 5;
const app = document.getElementById("app");

function section(title, ...children) {
  return el("section", { class: "section" }, el("h2", { class: "section__title" }, title), ...children);
}

function searchBox() {
  return el("a", { class: "input home-search", href: pages.search() },
    el("span", { "aria-hidden": "true" }, "🔍"), "게임 이름이나 키워드로 검색");
}

// ---------- 설문 맞춤 추천 (C7-d·C8-a: 취향 60% 이상·태그 4개 이상, 3장씩 자동 슬라이드, 최대 9장) ----------
function matchSection() {
  const box = el("div");
  api(`/api/feed?section=match&limit=${MATCH_LIMIT}&min_match=${MIN_MATCH}`).then(({ games, fallback }) => {
    const slider = gameSlider(games, { empty: "조건에 맞는 게임이 없어요. 설정에서 비선호를 줄여 보세요" });
    if (fallback && games.length) {
      box.replaceChildren(
        el("p", { class: "slider-note" }, `취향 ${MIN_MATCH}% 이상인 게임이 적어 비슷한 순서로 보여 드려요`), slider);
    } else {
      box.replaceChildren(slider);
    }
  });
  return section("설문 맞춤 추천", box);
}

// ---------- 추천 순위 상위 5개 (전체는 랭킹 페이지) ----------
function rankingSection() {
  const box = el("div", { class: "card rank-box" }, el("p", { class: "placeholder-text" }, "불러오는 중…"));
  api("/api/ranking").then(({ ranking }) => box.replaceChildren(rankList(ranking.slice(0, RANK_PREVIEW))));
  return el("section", { class: "section" },
    el("div", { class: "section__head" },
      el("h2", { class: "section__title" }, "추천 순위"),
      el("a", { class: "section__more", href: pages.ranking() }, "전체 보기 ›")),
    box);
}

// ---------- 최근 본 게임 ----------
function historySection() {
  const box = el("div");
  api("/api/me/history?limit=10").then(({ games }) => {
    if (!games.length) {
      box.replaceChildren(emptyState("아직 본 게임이 없어요", "👀"));
      return;
    }
    box.replaceChildren(el("div", { class: "recent-row" }, games.map((g) =>
      el("a", { class: "recent-item", href: pages.game(g.id) },
        el("span", { class: "recent-item__thumb" }, thumbnail(g)),
        el("span", { class: "recent-item__name" }, g.name),
        matchBadge(g.match, { short: true })))));
  });
  return section("최근 본 게임", box);
}

async function start() {
  const me = await api("/api/users", { method: "POST" });
  if (!me.survey_done) {
    location.replace(pages.survey());
    return;
  }
  app.append(
    topBar({ left: "theme", right: ["profile"], nickname: me.nickname }),
    searchBox(),
    matchSection(),
    rankingSection(),
    historySection(),
  );
  bottomNav("home");
}

start();

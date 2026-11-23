// 홈 (4.4 ②, C8-b 정리 → C12-b 순서)
// 상단바(로고 / 화면 모드·프로필) → 검색창 → 인기 랭킹 상위 5개(+ 전체 보기) → 이달의 게임 → 설문 맞춤 추천(슬라이드)
// → 추천 순위(게임픽 추천 수) 상위 5개 → 최근 본 게임 / 하단 메뉴
import {
  api, bottomNav, el, emptyState, gameRow, gameSlider, lineIcon, matchBadge, pages, rankList, thumbnail, topBar,
} from "/static/js/common.js";

const MATCH_LIMIT = 9, MIN_MATCH = 60;  // C8-a: 70 → 60 (서버가 태그 3개 이하 게임은 기준 통과 칸에서 뺌)
const RANK_PREVIEW = 5;
const app = document.getElementById("app");

function section(title, ...children) {
  return el("section", { class: "section" }, el("h2", { class: "section__title" }, title), ...children);
}

/** 제목 + 오른쪽 "전체 보기" 링크 */
function sectionWithMore(title, href, ...children) {
  return el("section", { class: "section" },
    el("div", { class: "section__head" },
      el("h2", { class: "section__title" }, title),
      el("a", { class: "section__more", href }, "전체 보기")),
    ...children);
}

function searchBox() {
  return el("a", { class: "input home-search", href: pages.search() },
    lineIcon("search", { size: 18 }), "게임 이름이나 키워드로 검색");
}

const loading = () => el("p", { class: "placeholder-text" }, "불러오는 중…");

// ---------- 인기 랭킹 상위 5개 (스토어 차트, C12-b·c) ----------
function popularSection() {
  const box = el("div", {}, loading());
  api("/api/popular?tab=all&limit=5", { silent: true })
    .then(({ games }) => box.replaceChildren(rankList(games, { kind: "store" })))
    .catch(() => box.replaceChildren(emptyState("인기 랭킹을 불러오지 못했어요")));
  return sectionWithMore("인기 랭킹", pages.ranking(), box);
}

// ---------- 이달의 게임 (C12-d) ----------
function monthlySection() {
  const title = el("h2", { class: "section__title" }, "이달의 게임");
  const box = el("div", {}, loading());
  api("/api/monthly", { silent: true })
    .then(({ month, games }) => {
      title.textContent = `${month}월의 게임`;
      box.replaceChildren(gameRow(games, { empty: "이번 달 게임을 고르는 중이에요" }));
    })
    .catch(() => box.replaceChildren(emptyState("이달의 게임을 불러오지 못했어요")));
  return el("section", { class: "section" }, title, box);
}

// ---------- 설문 맞춤 추천 (C7-d·C8-a: 취향 60% 이상·태그 4개 이상, 3장씩 자동 슬라이드, 최대 9장) ----------
function matchSection() {
  const box = el("div");
  api(`/api/feed?section=match&limit=${MATCH_LIMIT}&min_match=${MIN_MATCH}`).then(({ games, fallback }) => {
    const slider = gameSlider(games, { empty: "조건에 맞는 게임이 없어요. 설정에서 비선호를 줄일 수 있어요" });
    if (fallback && games.length) {
      box.replaceChildren(el("p", { class: "slider-note" }, `취향 ${MIN_MATCH}% 이상인 게임이 적어 가까운 순서로 보여요`), slider);
    } else {
      box.replaceChildren(slider);
    }
  });
  return section("설문 맞춤 추천", box);
}

// ---------- 추천 순위 상위 5개 (게임픽 추천 수, 전체는 랭킹 페이지) ----------
function likesSection() {
  const box = el("div", {}, loading());
  api("/api/ranking").then(({ ranking }) => box.replaceChildren(rankList(ranking.slice(0, RANK_PREVIEW))));
  return sectionWithMore("추천 순위", `${pages.ranking()}?kind=likes`, box);
}

// ---------- 최근 본 게임 ----------
function historySection() {
  const box = el("div");
  api("/api/me/history?limit=10").then(({ games }) => {
    if (!games.length) {
      box.replaceChildren(emptyState("아직 본 게임이 없어요"));
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
    popularSection(),
    monthlySection(),
    matchSection(),
    likesSection(),
    historySection(),
  );
  bottomNav("home");
}

start();

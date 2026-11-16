// 랭킹 (C8-b): 탭 2개 — 게임픽 추천 순위(따봉 수, /api/ranking) / 스토어 인기(/api/feed?section=popular, 순위 번호 + 카드)
// 주소 ?tab=store 로 스토어 인기 탭을 바로 열 수 있다
import { api, bottomNav, el, emptyState, gameCard, rankList, topBar } from "/static/js/common.js";

const STORE_PAGE = 30;  // /api/feed limit 최대
const app = document.getElementById("app");
const TABS = [
  { key: "pick", label: "게임픽 추천 순위" },
  { key: "store", label: "스토어 인기" },
];

async function pickTab() {
  const { ranking } = await api("/api/ranking");
  return el("div", { class: "card rank-box" }, rankList(ranking));
}

async function storeTab() {
  const { games } = await api(`/api/feed?section=popular&limit=${STORE_PAGE}`);
  if (!games.length) return emptyState("스토어 인기 순위를 불러오기 전이에요", "📈");
  return el("ol", { class: "store-rank" }, games.map((g) =>
    el("li", { class: "store-rank__item" },
      el("span", { class: "rank-item__no" }, `${g.chart_rank}`),
      gameCard(g, { maxTags: 2 }))));
}

function start() {
  const params = new URLSearchParams(location.search);
  const box = el("div");
  const bar = el("div", { class: "chips page-tabs", role: "tablist", "aria-label": "랭킹 종류" });
  const show = async (tab) => {
    bar.replaceChildren(...TABS.map((t) => el("button", {
      type: "button", role: "tab", class: `chip${t.key === tab.key ? " is-selected" : ""}`,
      "aria-selected": String(t.key === tab.key), onclick: () => show(t),
    }, t.label)));
    history.replaceState(null, "", tab.key === "store" ? "?tab=store" : location.pathname);
    box.replaceChildren(el("p", { class: "placeholder-text" }, "불러오는 중…"));
    box.replaceChildren(await (tab.key === "store" ? storeTab() : pickTab()));
  };
  app.append(
    topBar({ left: "back", right: ["home"] }),
    el("h1", { class: "section__title page-title" }, "랭킹"),
    bar, box);
  bottomNav("ranking");
  show(TABS.find((t) => t.key === params.get("tab")) || TABS[0]);
}

start();

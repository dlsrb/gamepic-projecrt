// 랭킹 (C8-b → C12-c)
// 위: 인기 랭킹(스토어 무료 차트, /api/popular) / 추천 순위(게임픽 사용자 추천 수, /api/ranking) 두 갈래를 나눠 보여 줌.
// 인기 랭킹 안 탭: 전체 / Google Play / App Store / 장르 탭(서버 reco_config.RANKING_GENRE_TABS) — 탭 목록은 서버가 줌.
// 주소: ?kind=likes (추천 순위), ?tab=play 등 (인기 랭킹 탭). 예전 ?tab=store는 인기 랭킹 전체로.
import { api, bottomNav, el, rankList, topBar } from "/static/js/common.js";

const app = document.getElementById("app");
const KINDS = [
  { key: "popular", label: "인기 랭킹", desc: "Google Play·App Store 무료 인기 차트 기준이에요" },
  { key: "likes", label: "추천 순위", desc: "게임픽 사용자가 추천한 수 기준이에요" },
];

function segmented(items, current, onPick, label) {
  return el("div", { class: "segmented", role: "tablist", "aria-label": label }, items.map((item) => el("button", {
    type: "button", role: "tab", class: `segmented__item${item.key === current ? " is-selected" : ""}`,
    "aria-selected": String(item.key === current), onclick: () => onPick(item.key),
  }, item.label)));
}

function start() {
  const params = new URLSearchParams(location.search);
  let kind = params.get("kind") === "likes" ? "likes" : "popular";
  let tab = params.get("tab") && params.get("tab") !== "store" ? params.get("tab") : "all";
  let tabs = [{ key: "all", label: "전체" }];
  let request = 0;

  const kindBar = el("div");
  const desc = el("p", { class: "page-desc" });
  const tabBar = el("div");
  const box = el("div");

  const save = () => {
    const query = new URLSearchParams();
    if (kind === "likes") query.set("kind", "likes");
    else if (tab !== "all") query.set("tab", tab);
    history.replaceState(null, "", query.toString() ? `?${query}` : location.pathname);
  };

  const show = async () => {
    const mine = ++request;
    save();
    kindBar.replaceChildren(segmented(KINDS, kind, (k) => { kind = k; show(); }, "랭킹 종류"));
    desc.textContent = KINDS.find((k) => k.key === kind).desc;
    tabBar.replaceChildren(kind === "popular"
      ? el("div", { class: "chips page-tabs", role: "tablist", "aria-label": "인기 랭킹 분류" }, tabs.map((t) => el("button", {
        type: "button", role: "tab", class: `chip${t.key === tab ? " is-selected" : ""}`,
        "aria-selected": String(t.key === tab), onclick: () => { tab = t.key; show(); },
      }, t.label)))
      : "");
    box.replaceChildren(el("p", { class: "placeholder-text" }, "불러오는 중…"));
    try {
      if (kind === "likes") {
        const { ranking } = await api("/api/ranking");
        if (mine === request) box.replaceChildren(rankList(ranking, { kind: "likes" }));
        return;
      }
      const data = await api(`/api/popular?tab=${encodeURIComponent(tab)}`, { silent: true });
      if (mine !== request) return;
      if (tabs.length !== data.tabs.length) {  // 첫 응답에서 탭 목록 받기
        tabs = data.tabs;
        show();
        return;
      }
      box.replaceChildren(rankList(data.games, { kind: "store", empty: "이 분류에는 차트에 든 게임이 아직 없어요" }));
    } catch {
      if (mine !== request) return;
      if (kind === "popular" && tab !== "all") {  // 없는 탭 주소로 들어온 경우 전체로
        tab = "all";
        show();
        return;
      }
      box.replaceChildren(el("p", { class: "placeholder-text" }, "랭킹을 불러오지 못했어요"));
    }
  };

  app.append(topBar({ left: "back" }), el("h1", { class: "page-title" }, "랭킹"), kindBar, desc, tabBar, box);
  bottomNav("ranking");
  show();
}

start();

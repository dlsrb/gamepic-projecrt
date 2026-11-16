// 신작 (C8-b): 출시 90일 이내 게임 목록 (/api/feed?section=new, C8-a 정렬: 한글 이름·별점 있는 게임 먼저 → 나머지, 각각 최신순)
// 카드 아래 출시일, 20개씩 "더 보기" (offset)
import { api, bottomNav, el, emptyState, gameCard, topBar } from "/static/js/common.js";

const PAGE = 20;
const app = document.getElementById("app");

/** "2026-11-07" → "11/7 출시" */
function releaseLabel(date) {
  const [, m, d] = (date || "").split("-").map(Number);
  return m && d ? `${m}/${d} 출시` : "";
}

function newCard(game) {
  const card = gameCard(game, { maxTags: 2 });
  card.append(el("span", { class: "game-card__date" }, releaseLabel(game.release_date)));
  return card;
}

function start() {
  let offset = 0;
  const grid = el("div", { class: "grid-3" });
  const more = el("button", { type: "button", class: "btn btn--ghost btn--block page-more", hidden: true }, "더 보기");
  const box = el("div", {}, el("p", { class: "placeholder-text" }, "불러오는 중…"));
  const load = async () => {
    more.disabled = true;
    try {
      const { games, has_more: hasMore } = await api(`/api/feed?section=new&limit=${PAGE}&offset=${offset}`);
      if (offset === 0) box.replaceChildren(games.length ? grid : emptyState("최근 90일 안에 나온 게임이 아직 없어요", "✨"));
      grid.append(...games.map(newCard));
      offset += games.length;
      more.hidden = !hasMore;
    } finally {
      more.disabled = false;
    }
  };
  more.addEventListener("click", load);
  app.append(
    topBar({ left: "back", right: ["home"] }),
    el("h1", { class: "section__title page-title" }, "신작"),
    el("p", { class: "page-desc" }, "최근 90일 안에 출시된 게임이에요"),
    box, more);
  bottomNav("new");
  load();
}

start();

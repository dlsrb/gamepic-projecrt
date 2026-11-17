// 검색 (4.4 ③, 4.4.1 와이어프레임 03·04, 4.11 C7-c)
// 입력 전: 검색창 → 최근 검색어(내 기록, 이 브라우저에만) + 추천 검색어(서버) / 입력 중: 같은 자리에 자동완성
// 결과: 검색창 → 단어마다 키워드 칩 → 게임 카드 3열 (태그 칩 + 취향 부합도)
// 여러 키워드는 띄어쓰기로 (예: "무료 2D" = 모두 만족하는 게임). 단어 해석은 서버가 한다
// 최근·추천 검색어나 자동완성을 누르면 검색창에 입력만 되고 바로 검색되지 않는다 (4.4 ③)
import { api, bottomNav, el, gameGrid, tagChip, topBar } from "/static/js/common.js";

const MAX_LENGTH = 60, MAX_WORDS = 10, SUGGEST_DELAY_MS = 200;
const RECENT_KEY = "gp-recent-searches", RECENT_MAX = 10;
const app = document.getElementById("app");

let q = "";                      // 지금 검색한 말 (띄어쓰기로 여러 단어)
let suggestTimer = null;

const input = el("input", {
  class: "input", type: "search", name: "q", maxlength: String(MAX_LENGTH), autocomplete: "off",
  placeholder: "게임 이름이나 키워드 (예: 무료 2D)", "aria-label": "검색어",
});
const suggestBox = el("div", { class: "card search-suggest", "aria-live": "polite" });
const keywordBox = el("div", { class: "chips search-keywords" });
const results = el("div", { class: "search-results" });

const words = (text) => text.trim().split(/\s+/).filter(Boolean).slice(0, MAX_WORDS);

// ---------- 최근 검색어 (서버에 보내지 않음) ----------
function loadRecent() {
  try {
    const list = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
    return Array.isArray(list) ? list.filter((x) => typeof x === "string").slice(0, RECENT_MAX) : [];
  } catch {
    return [];
  }
}

function saveRecent(list) {
  try {
    if (list.length) localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, RECENT_MAX)));
    else localStorage.removeItem(RECENT_KEY);
  } catch {
    // 저장소를 못 쓰는 환경이면 기록 없이
  }
}

function addRecent(text) {
  saveRecent([text, ...loadRecent().filter((x) => x !== text)]);
}

// ---------- 최근·추천 검색어 / 자동완성 ----------
function fill(text) {
  input.value = text;  // 입력만. 검색은 사용자가 검색 버튼(엔터)을 눌러야
  input.focus();
  showSuggestions();
}

function recentSection() {
  const recent = loadRecent();
  if (!recent.length) return null;
  return el("div", { class: "search-recent" },
    el("div", { class: "search-suggest__head" },
      el("h2", { class: "search-suggest__title" }, "최근 검색어"),
      el("button", { type: "button", class: "search-clear", onclick: () => { saveRecent([]); showSuggestions(); } }, "전체 지우기")),
    el("div", { class: "chips" }, recent.map((text) =>
      el("span", { class: "recent-chip" },
        el("button", { type: "button", class: "chip", onclick: () => fill(text) }, text),
        el("button", {
          type: "button", class: "recent-chip__remove", "aria-label": `최근 검색어 ${text} 지우기`,
          onclick: () => { saveRecent(loadRecent().filter((x) => x !== text)); showSuggestions(); },
        }, "×")))));
}

async function showStart() {
  const { keywords } = await api("/api/search/popular");
  suggestBox.replaceChildren(...[
    recentSection(),
    el("h2", { class: "search-suggest__title" }, "추천 검색어"),
    el("div", { class: "chips" }, keywords.map((k) => tagChip(k.keyword, { onClick: () => fill(k.keyword) }))),
  ].filter(Boolean));
}

async function showSuggestions() {
  const text = input.value;
  if (!text.trim()) return showStart();
  // 자동완성은 마지막 단어로. 고르면 그 단어만 바뀜 ("무료 2" → "무료 2D")
  const list = words(text);
  const last = /\s$/.test(text) ? "" : list[list.length - 1];
  if (!last) return showStart();
  const { suggestions } = await api(`/api/search/suggest?q=${encodeURIComponent(last)}`, { silent: true })
    .catch(() => ({ suggestions: [] }));
  if (input.value !== text) return;  // 그사이 더 입력했으면 버림
  const head = list.slice(0, -1).join(" ");
  suggestBox.replaceChildren(
    el("h2", { class: "search-suggest__title" }, "자동완성"),
    suggestions.length
      ? el("ul", { class: "suggest-list" }, suggestions.map((s) =>
        el("li", {}, el("button", { type: "button", class: "suggest-item", onclick: () => fill(head ? `${head} ${s.text}` : s.text) },
          el("span", { class: "suggest-item__type" }, s.type === "game" ? "게임" : "키워드"), s.text))))
      : el("p", { class: "placeholder-text" }, "맞는 게임·키워드가 없어요"));
}

input.addEventListener("input", () => {
  clearTimeout(suggestTimer);
  suggestTimer = setTimeout(showSuggestions, SUGGEST_DELAY_MS);
});
input.addEventListener("focus", () => { suggestBox.hidden = false; showSuggestions(); });

// ---------- 키워드 칩 + 검색 ----------
function chipLabel(keyword) {
  if (keyword.kind === "synonym") return `${keyword.word} · ${keyword.label}`;
  return keyword.word;
}

function renderKeywords(keywords) {
  keywordBox.replaceChildren(...keywords.map((k, i) => tagChip(chipLabel(k), {
    selected: true, removable: true,
    onClick: () => {  // 칩을 누르면 그 단어만 빼고 다시 검색
      const rest = words(q).filter((_, j) => j !== i).join(" ");
      input.value = rest;
      search(rest);
    },
  })));
  keywordBox.hidden = keywords.length === 0;
}

function syncUrl() {
  history.replaceState(null, "", `${location.pathname}${q ? `?q=${encodeURIComponent(q)}` : ""}`);
}

async function search(text) {
  q = words(text).join(" ").slice(0, MAX_LENGTH);
  syncUrl();
  if (!q) {
    renderKeywords([]);
    results.replaceChildren();
    suggestBox.hidden = false;
    return showStart();
  }
  suggestBox.hidden = true;
  const data = await api(`/api/search?q=${encodeURIComponent(q)}`);
  renderKeywords(data.keywords);
  results.replaceChildren(
    el("p", { class: "search-count" }, `게임 ${data.games.length}개`),
    gameGrid(data.games, { maxTags: 2, empty: "모든 키워드에 맞는 게임이 없어요. 키워드를 줄여 보세요" }));
}

function submit(event) {
  event.preventDefault();
  const text = words(input.value).join(" ");
  input.value = text;
  input.blur();
  if (text) addRecent(text);
  search(text);
}

function start() {
  q = new URLSearchParams(location.search).get("q") || "";
  input.value = q;
  app.append(
    topBar({ left: "back" }),
    el("form", { class: "search-form", role: "search", onsubmit: submit },
      input, el("button", { type: "submit", class: "btn btn--primary" }, "검색")),
    suggestBox, keywordBox, results);
  bottomNav();
  search(q);
}

start();

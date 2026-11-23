/*
 * 게임픽 공통 부품 (PROJECT.md 4.11 C5). 페이지에서 ES 모듈로 불러 쓴다:
 *   import { api, topBar, gameCard } from "/static/js/common.js";
 * 화면 글자는 전부 textContent로 넣는다 (innerHTML 금지 → 서버 데이터로 인한 XSS 방지).
 */

// ---------- 페이지 주소 (C6에서 바뀌면 여기만 고친다) ----------
export const pages = {
  survey: () => "/static/pages/survey.html",
  home: () => "/static/pages/home.html",
  search: (q) => "/static/pages/search.html" + (q ? `?q=${encodeURIComponent(q)}` : ""),
  game: (id) => `/static/pages/game.html?id=${encodeURIComponent(id)}`,
  mypage: () => "/static/pages/mypage.html",
  settings: () => "/static/pages/settings.html",
  ranking: () => "/static/pages/ranking.html",   // C8-b
  newGames: () => "/static/pages/new.html",      // C8-b
};

// ---------- DOM 도우미 ----------
/** el("div", {class: "card", onclick: fn}, "글자", 자식요소 ...) */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// ---------- 화면 모드 (theme.js가 첫 화면에 먼저 적용) ----------
const THEME_KEY = "gp-theme";
const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

/** 저장된 선택: "light" | "dark" | "system" */
export function getTheme() {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    return saved === "light" || saved === "dark" ? saved : "system";
  } catch {
    return "system";
  }
}

/** 지금 실제로 보이는 모드: "light" | "dark" */
export function effectiveTheme() {
  const theme = getTheme();
  if (theme !== "system") return theme;
  return darkQuery.matches ? "dark" : "light";
}

export function setTheme(theme) {
  try {
    if (theme === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, theme);
  } catch {
    // 저장 못 해도 이번 화면에는 적용
  }
  if (theme === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", theme);
  document.dispatchEvent(new CustomEvent("gp:themechange", { detail: effectiveTheme() }));
}

export function toggleTheme() {
  setTheme(effectiveTheme() === "dark" ? "light" : "dark");
}

darkQuery.addEventListener("change", () => {
  if (getTheme() === "system") document.dispatchEvent(new CustomEvent("gp:themechange", { detail: effectiveTheme() }));
});

// ---------- 알림 ----------
function toastArea() {
  let area = document.querySelector(".toast-area");
  if (!area) {
    area = el("div", { class: "toast-area", "aria-live": "polite" });
    document.body.append(area);
  }
  return area;
}

/** 화면 아래 잠깐 뜨는 알림. kind: "info" | "error" */
export function toast(message, kind = "info", ms = 3000) {
  const node = el("div", { class: `toast toast--${kind}`, role: kind === "error" ? "alert" : "status" }, message);
  toastArea().append(node);
  setTimeout(() => node.remove(), ms);
  return node;
}

// ---------- API ----------
export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

/**
 * 서버 API 호출. 쿠키(임시 사용자)는 브라우저가 알아서 보낸다.
 * 실패하면 서버의 {"error": "..."} 문구로 알림을 띄우고 ApiError를 던진다 (silent: true면 알림 없이 던지기만).
 */
export async function api(path, { method = "GET", body, silent = false } = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: body === undefined ? { Accept: "application/json" } : { Accept: "application/json", "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    const error = new ApiError("서버에 연결할 수 없습니다. 잠시 후 다시 시도하세요.", 0);
    if (!silent) toast(error.message, "error");
    throw error;
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new ApiError(data?.error || `요청을 처리하지 못했습니다 (${response.status})`, response.status);
    if (!silent) toast(error.message, "error");
    throw error;
  }
  return data;
}

// ---------- 상단바 ----------
function themeButton() {
  const button = el("button", { type: "button", class: "icon-btn icon-btn--round", onclick: toggleTheme });
  const render = () => {
    const dark = effectiveTheme() === "dark";
    button.replaceChildren(lineIcon(dark ? "sun" : "moon", { size: 18 }));  // C12-f: 이모지 대신 선 아이콘
    button.setAttribute("aria-label", dark ? "라이트 모드로 바꾸기" : "다크 모드로 바꾸기");
  };
  render();
  document.addEventListener("gp:themechange", render);
  return button;
}

function backButton() {
  const goBack = () => (history.length > 1 ? history.back() : location.assign(pages.home()));
  return el("button", { type: "button", class: "icon-btn icon-btn--round", "aria-label": "뒤로가기", onclick: goBack },
    lineIcon("back", { size: 18 }));
}

/**
 * 로고 GAMEPICK (C11-b). 이미지가 아니라 글자: GAME = 남색, PICK = 밝은 회청색 채움 + 남색 얇은 테두리 (다크는 밝은 색).
 * 기본은 누르면 홈(/). onClick을 주면 링크 대신 버튼(설문의 "그만둘까요?" 확인), static이면 누를 수 없는 글자.
 */
export function logo({ onClick = null, isStatic = false } = {}) {
  const word = [el("span", { class: "logo__game" }, "GAME"), el("span", { class: "logo__pick" }, "PICK")];
  if (isStatic) return el("span", { class: "logo", role: "img", "aria-label": "GamePick" }, word);
  if (onClick) return el("button", { type: "button", class: "logo", "aria-label": "GamePick 홈으로", onclick: onClick }, word);
  return el("a", { class: "logo", href: "/", "aria-label": "GamePick 홈으로" }, word);
}

/**
 * 상단바. 왼쪽: (← 뒤로) + 로고(누르면 홈), 오른쪽: 버튼 목록.
 *   topBar({ left: "theme", right: ["profile"], nickname })   // 홈: 로고만, 화면 모드 버튼은 오른쪽으로 (C11-b)
 *   topBar({ left: "back", right: [likeButtonElement] })      // 상세
 * right에는 "profile" | "settings" | "theme" 또는 직접 만든 요소. 오른쪽 위 "홈" 버튼은 로고가 대신해서 없앰 (C11-b)
 */
export function topBar({ left = "back", right = [], nickname = null } = {}) {
  const make = {
    theme: themeButton,
    back: backButton,
    settings: () => el("a", { class: "icon-btn", href: pages.settings() }, "설정"),
    profile: () => el("a", { class: "profile-dot", href: pages.mypage(), "aria-label": "마이페이지" },
      nickname ? nickname.slice(0, 1) : "나"),
  };
  const build = (item) => (item instanceof Node ? item : make[item]());
  const rightItems = [...(left === "theme" ? ["theme"] : []), ...right.filter((item) => item !== "home")];
  return el("header", { class: "topbar" },
    el("div", { class: "topbar__side" }, left === "back" ? backButton() : null, logo()),
    el("div", { class: "topbar__side" }, rightItems.map(build)));
}

// ---------- 하단 메뉴 (C8-b) ----------
const NAV_ITEMS = [
  { key: "home", label: "홈", icon: "home", href: () => pages.home() },          // C12-f: 이모지 → 선 아이콘
  { key: "ranking", label: "랭킹", icon: "chart", href: () => pages.ranking() },
  { key: "new", label: "신작", icon: "calendar", href: () => pages.newGames() },
  { key: "settings", label: "설정", icon: "sliders", href: () => pages.settings() },
];

/**
 * 화면 아래 고정 메뉴. active: "home" | "ranking" | "new" | "settings" | null(강조 없음: 상세·검색·마이페이지)
 * body 끝에 붙이고, 본문이 가리지 않게 body에 has-bottom-nav 클래스(아래 여백)를 단다. 설문 페이지에서는 부르지 않는다.
 */
export function bottomNav(active = null) {
  document.querySelector(".bottom-nav")?.remove();
  const nav = el("nav", { class: "bottom-nav", "aria-label": "주 메뉴" },
    el("ul", { class: "bottom-nav__list" }, NAV_ITEMS.map((item) =>
      el("li", {}, el("a", {
        class: `bottom-nav__item${item.key === active ? " is-active" : ""}`,
        href: item.href(),
        "aria-current": item.key === active ? "page" : null,
      }, el("span", { class: "bottom-nav__icon", "aria-hidden": "true" }, lineIcon(item.icon, { size: 20 })),
      el("span", { class: "bottom-nav__label" }, item.label))))));
  document.body.classList.add("has-bottom-nav");
  document.body.append(nav);
  return nav;
}

// ---------- 칩·부합도·빈 값 ----------
/** 태그 칩. onClick을 주면 누를 수 있는 버튼(설문 보기·검색 키워드), 아니면 표시만 */
export function tagChip(text, { selected = false, onClick = null, removable = false } = {}) {
  const cls = `chip${selected ? " is-selected" : ""}${removable ? " chip--removable" : ""}`;
  if (!onClick) return el("span", { class: cls }, text);
  return el("button", { type: "button", class: cls, "aria-pressed": String(selected), onclick: onClick }, text);
}

/** 취향 부합도 %. short: 좁은 카드용 짧은 글자 ("취향 83%") */
export function matchBadge(match, { short = false } = {}) {
  const label = short ? "취향" : "취향 부합도";
  if (match === null || match === undefined) return el("span", { class: "match match--none" }, `${label} -`);
  return el("span", { class: "match", title: `내 취향과 맞는 정도 ${match}%` }, `${label} ${match}%`);
}

/** 자동 배지 (신작·인기·급상승·인디, C12-e). max개까지, 작은 글자 */
export function badgeRow(badges, { max = 2 } = {}) {
  if (!badges || !badges.length) return null;
  return el("span", { class: "badges" }, badges.slice(0, max).map((b) =>
    el("span", { class: `badge${b === "급상승" ? " badge--accent" : ""}` }, b)));
}

/** 값이 비었을 때 대체 표시 (게임 정보·리뷰 분석이 아직 없을 때). C12-f: 장식 이모지 없음 */
export function emptyState(message = "정보 준비 중") {
  return el("div", { class: "empty" }, message);
}

/** 글자 값이 비면 "정보 준비 중" 회색 글자로 */
export function textOr(value, fallback = "정보 준비 중") {
  return value ? el("span", {}, value) : el("span", { class: "placeholder-text" }, fallback);
}

/** 썸네일. 주소가 없거나 못 불러오면 단색 바탕 + 이름 첫 글자 (C12-f: 그라데이션 없앰) */
export function thumbnail(game) {
  const fallback = () => el("div", { class: "thumb-fallback", "aria-hidden": "true" },
    el("span", { class: "thumb-fallback__letter" }, (game.name || "?").slice(0, 1)),
    el("span", { class: "thumb-fallback__note" }, "이미지 준비 중"));
  if (!game.thumbnail_url) return fallback();
  const img = el("img", { src: game.thumbnail_url, alt: "", loading: "lazy" });
  img.addEventListener("error", () => img.replaceWith(fallback()), { once: true });
  return img;
}

// ---------- 게임 카드 ----------
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

/**
 * 게임 카드 (API의 card 형식: id, name, tags, badges, thumbnail_url, match, slot ...)
 * 누르면 상세로. 자동 배지(C12-e)는 이름 위 작은 글자로 2개까지, 태그 칩은 maxTags개까지.
 * C12-f: 썸네일 위 "새로운 발견" 표시는 없앰 (탐색 칸도 같은 카드)
 */
export function gameCard(game, { maxTags = 2, showMatch = true } = {}) {
  return el("a", { class: "game-card", href: pages.game(game.id) },
    el("div", { class: "game-card__thumb" }, thumbnail(game)),
    badgeRow(game.badges),
    el("span", { class: "game-card__name" }, game.name),
    game.disliked ? el("span", { class: "game-card__note" }, "내 비선호 태그 포함") : null,  // 검색에서 이름으로 찾아 남은 비선호 게임 (C12-k)
    game.note ? el("span", { class: "game-card__note" }, game.note) : null,  // 검색 일부 일치 "○○만 맞음" (C15-b)
    el("div", { class: "chips" }, (game.tags || []).slice(0, maxTags).map((t) => tagChip(t))),
    showMatch ? matchBadge(game.match, { short: true }) : null);
}

/** 가로로 넘기는 카드 줄 (이달의 게임, C12-d). 자동 넘김 없음 */
export function gameRow(games, { empty = "보여 줄 게임이 아직 없어요", ...cardOptions } = {}) {
  if (!games || games.length === 0) return emptyState(empty);
  return el("div", { class: "game-row" }, games.map((g) => gameCard(g, cardOptions)));
}

/**
 * 카드 가로 슬라이드 (C7-d). 한 화면에 3장, 약 4초마다 다음 쪽(마지막 다음은 처음), 아래 점으로 위치 표시.
 * 손가락으로 밀어 넘기기 가능. 누르고 있거나 마우스를 올리거나 안에 포커스가 있으면 멈춤.
 * 기기의 "동작 줄이기" 설정이면 자동 넘김 없음. 카드가 3장 이하면 점·자동 넘김 없음.
 */
export function gameSlider(games, { empty = "보여 줄 게임이 아직 없어요", perPage = 3, interval = 4000, ...cardOptions } = {}) {
  if (!games || games.length === 0) return emptyState(empty);
  const track = el("div", { class: "slider__track" }, games.map((g) => gameCard(g, cardOptions)));
  const slider = el("div", { class: "slider" }, track);
  const pageCount = Math.ceil(games.length / perPage);
  if (pageCount <= 1) return slider;

  const gapOf = () => parseFloat(getComputedStyle(track).columnGap) || 0;
  const pageWidth = () => track.clientWidth + gapOf();
  const current = () => {
    const width = pageWidth();  // 화면에 붙기 전에는 0
    return width > 0 ? Math.min(pageCount - 1, Math.round(track.scrollLeft / width)) : 0;
  };
  const goTo = (page) => track.scrollTo({ left: page * pageWidth(), behavior: reduceMotion.matches ? "auto" : "smooth" });

  const dots = Array.from({ length: pageCount }, (_, i) => el("button", {
    type: "button", class: "slider__dot", "aria-label": `${i + 1}쪽 보기`, onclick: () => goTo(i),
  }));
  const markDots = () => {
    const page = current();
    dots.forEach((dot, i) => dot.setAttribute("aria-current", String(i === page)));
  };
  markDots();
  track.addEventListener("scroll", markDots, { passive: true });

  // 자동 넘김: 멈춤 이유가 하나라도 있으면 쉰다
  const holds = new Set();
  const hold = (reason) => () => holds.add(reason);
  const release = (reason) => () => holds.delete(reason);
  slider.addEventListener("pointerenter", hold("hover"));
  slider.addEventListener("pointerleave", release("hover"));
  slider.addEventListener("pointerdown", hold("press"));
  window.addEventListener("pointerup", release("press"));
  window.addEventListener("pointercancel", release("press"));
  slider.addEventListener("focusin", hold("focus"));
  slider.addEventListener("focusout", release("focus"));
  let attached = false;
  const timer = setInterval(() => {
    if (slider.isConnected) attached = true;
    else if (attached) clearInterval(timer);  // 화면에서 빠진 슬라이드는 정리
    if (reduceMotion.matches || holds.size || document.hidden || !slider.isConnected) return;
    const page = current();
    if (page >= pageCount - 1) goTo(0);
    else track.scrollBy({ left: pageWidth(), behavior: "smooth" });
  }, interval);

  return el("div", { class: "slider-wrap" }, slider, el("div", { class: "slider__dots" }, dots));
}

// ---------- 선 아이콘 (인라인 SVG, innerHTML 없이 만듦) ----------
// 모양은 Lucide 아이콘(ISC 라이선스)의 path. 화면의 장식 이모지는 전부 이것으로 (C12-f)
const SVG_NS = "http://www.w3.org/2000/svg";
const ICON_PATHS = {
  thumb: ["M7 10v12", "M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"],
  thumbDown: ["M17 14V2", "M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"],
  home: ["M3 10 12 3l9 7v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z", "M9 22V12h6v10"],
  chart: ["M3 3v18h18", "M18 17V9", "M13 17V5", "M8 17v-3"],
  calendar: ["M8 2v4", "M16 2v4", "M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z", "M3 10h18"],
  sliders: ["M21 4h-7", "M10 4H3", "M21 12h-9", "M8 12H3", "M21 20h-5", "M12 20H3", "M14 2v4", "M8 10v4", "M16 18v4"],
  search: ["M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z", "m21 21-4.3-4.3"],
  moon: ["M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"],
  sun: ["M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", "M12 2v2", "M12 20v2", "m4.93 4.93 1.41 1.41", "m17.66 17.66 1.41 1.41",
    "M2 12h2", "M20 12h2", "m6.34 17.66-1.41 1.41", "m19.07 4.93-1.41 1.41"],
  back: ["m12 19-7-7 7-7", "M19 12H5"],
};

export function lineIcon(name, { size = 16, className = "line-icon" } = {}) {
  const svg = document.createElementNS(SVG_NS, "svg");
  for (const [key, value] of Object.entries({
    viewBox: "0 0 24 24", width: size, height: size, fill: "none", stroke: "currentColor", "stroke-width": 2,
    "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false", class: className,
  })) svg.setAttribute(key, value);
  for (const d of ICON_PATHS[name]) {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

/** 랭킹 줄 오른쪽: 스토어 차트 순위 ("Play 3 · iOS 5") */
function storeRanks(g) {
  const parts = [g.chart_rank_android ? `Play ${g.chart_rank_android}` : null, g.chart_rank_ios ? `iOS ${g.chart_rank_ios}` : null];
  return el("span", { class: "rank-item__side" }, parts.filter(Boolean).join(" · "));
}

/**
 * 순위 목록 (C11-c 줄 모양: 순위 숫자 · 정사각 썸네일 · 제목 + 한 줄 설명(없으면 장르·태그) · 오른쪽 값)
 * kind: "likes" = 추천 순위 (/api/ranking, 오른쪽 엄지 + 추천 수) / "store" = 인기 랭킹 (/api/popular, 오른쪽 스토어 순위, C12-c)
 */
export function rankList(ranking, { kind = "likes", empty = null } = {}) {
  if (!ranking.length) {
    return emptyState(empty || (kind === "store" ? "스토어 순위를 아직 받지 못했어요" : "아직 추천을 받은 게임이 없어요"));
  }
  // 차트 밖 게임(rank null, C12-k)은 순위 숫자 대신 "—", 처음 나오는 곳 위에 "차트 밖" 구분 줄
  const firstOffChart = ranking.findIndex((g) => g.rank === null || g.rank === undefined);
  return el("ol", { class: "rank-list" }, ranking.flatMap((g, i) => {
    const sub = g.summary || [...new Set([g.genre, ...(g.tags || []).slice(0, 2)])].filter(Boolean).join(" · ");
    const side = kind === "store" ? storeRanks(g)
      : el("span", { class: "rank-item__side", "aria-label": `추천 ${g.like_count}` }, lineIcon("thumb"), el("span", {}, `${g.like_count}`));
    const ranked = typeof g.rank === "number";
    const divider = kind === "store" && i === firstOffChart && i > 0
      ? el("li", { class: "rank-divider", "aria-hidden": "true" }, "차트 밖 — 설치 수·별점 수 순") : null;
    return [divider, el("li", {},
      el("a", { class: `rank-item${ranked && g.rank <= 3 ? " rank-item--top" : ""}`, href: pages.game(g.id) },
        el("span", { class: "rank-item__no", "aria-label": ranked ? `${g.rank}위` : "차트 밖" }, ranked ? `${g.rank}` : "—"),
        el("span", { class: "rank-item__thumb" }, thumbnail(g)),
        el("span", { class: "rank-item__text" },
          el("span", { class: "rank-item__name" }, g.name),
          sub ? el("span", { class: "rank-item__sub" }, sub) : null),
        side))].filter(Boolean);
  }));
}

/** 카드 3열 격자. 목록이 비면 대체 표시 */
export function gameGrid(games, { empty = "보여 줄 게임이 아직 없어요", ...cardOptions } = {}) {
  if (!games || games.length === 0) return emptyState(empty);
  return el("div", { class: "grid-3" }, games.map((g) => gameCard(g, cardOptions)));
}

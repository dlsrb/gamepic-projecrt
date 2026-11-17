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
    button.textContent = dark ? "☀️" : "🌙";
    button.setAttribute("aria-label", dark ? "라이트 모드로 바꾸기" : "다크 모드로 바꾸기");
  };
  render();
  document.addEventListener("gp:themechange", render);
  return button;
}

function backButton() {
  const goBack = () => (history.length > 1 ? history.back() : location.assign(pages.home()));
  return el("button", { type: "button", class: "icon-btn icon-btn--round", "aria-label": "뒤로가기", onclick: goBack }, "←");
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
 *   topBar({ left: "theme", right: ["profile"], nickname })   // 홈: 로고만, 🌙은 오른쪽으로 (C11-b)
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
  { key: "home", label: "홈", icon: "🏠", href: () => pages.home() },
  { key: "ranking", label: "랭킹", icon: "🏆", href: () => pages.ranking() },
  { key: "new", label: "신작", icon: "✨", href: () => pages.newGames() },
  { key: "settings", label: "설정", icon: "⚙️", href: () => pages.settings() },
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
      }, el("span", { class: "bottom-nav__icon", "aria-hidden": "true" }, item.icon),
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

/** 값이 비었을 때 대체 표시 (게임 정보·리뷰 분석이 아직 없을 때) */
export function emptyState(message = "정보 준비 중", icon = "🎮") {
  return el("div", { class: "empty" }, el("span", { class: "empty__icon", "aria-hidden": "true" }, icon), message);
}

/** 글자 값이 비면 "정보 준비 중" 회색 글자로 */
export function textOr(value, fallback = "정보 준비 중") {
  return value ? el("span", {}, value) : el("span", { class: "placeholder-text" }, fallback);
}

/** 썸네일. 주소가 없거나 못 불러오면 그라데이션 + 이름 첫 글자 */
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
 * 게임 카드 (API의 card 형식: id, name, tags, thumbnail_url, match, slot ...)
 * 누르면 상세로. 태그 칩은 maxTags개까지.
 */
export function gameCard(game, { maxTags = 2, showMatch = true } = {}) {
  return el("a", { class: "game-card", href: pages.game(game.id) },
    el("div", { class: "game-card__thumb" },
      thumbnail(game),
      game.slot === "탐색" ? el("span", { class: "game-card__slot" }, "새로운 발견") : null),
    el("span", { class: "game-card__name" }, game.name),
    el("div", { class: "chips" }, (game.tags || []).slice(0, maxTags).map((t) => tagChip(t))),
    showMatch ? matchBadge(game.match, { short: true }) : null);
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
const SVG_NS = "http://www.w3.org/2000/svg";
const ICON_PATHS = {
  // 엄지 (Lucide thumbs-up, ISC 라이선스)
  thumb: ["M7 10v12", "M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"],
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

/**
 * 추천(따봉) 순위 목록 (홈 상위 5개·랭킹 페이지 공통, /api/ranking 형식). C11-c:
 * 순위 숫자(동그라미 없이, 1~3위 굵게) · 정사각 썸네일 · 제목 + 한 줄 설명(없으면 장르·태그 2개) · 선 아이콘 엄지 + 추천 수
 */
export function rankList(ranking) {
  if (!ranking.length) return emptyState("아직 추천을 받은 게임이 없어요", "🏆");
  return el("ol", { class: "rank-list" }, ranking.map((g) => {
    const sub = g.summary || [g.genre, ...(g.tags || []).slice(0, 2)].filter(Boolean).join(" · ");
    return el("li", {},
      el("a", { class: `rank-item${g.rank <= 3 ? " rank-item--top" : ""}`, href: pages.game(g.id) },
        el("span", { class: "rank-item__no" }, `${g.rank}`),
        el("span", { class: "rank-item__thumb" }, thumbnail(g)),
        el("span", { class: "rank-item__text" },
          el("span", { class: "rank-item__name" }, g.name),
          sub ? el("span", { class: "rank-item__sub" }, sub) : null),
        el("span", { class: "rank-item__likes", "aria-label": `추천 ${g.like_count}` },
          lineIcon("thumb"), el("span", {}, `${g.like_count}`))));
  }));
}

/** 카드 3열 격자. 목록이 비면 대체 표시 */
export function gameGrid(games, { empty = "보여 줄 게임이 아직 없어요", ...cardOptions } = {}) {
  if (!games || games.length === 0) return emptyState(empty);
  return el("div", { class: "grid-3" }, games.map((g) => gameCard(g, cardOptions)));
}

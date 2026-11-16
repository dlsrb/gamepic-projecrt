// _preview.html: 공통 부품을 한 화면에 모아 라이트/다크로 확인 (개발용)
import {
  api, effectiveTheme, el, emptyState, gameGrid, matchBadge, setTheme, tagChip, textOr, toast, topBar,
} from "/static/js/common.js";

const SAMPLE_IMAGE = "data:image/svg+xml," + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 3 4"><rect width="3" height="4" fill="#7a9e7e"/>' +
  '<circle cx="1.5" cy="1.6" r="0.8" fill="#f2e3c6"/></svg>');

const sampleGames = [
  { id: "dq8", name: "드래곤 퀘스트 VIII", tags: ["턴제RPG", "3D", "싱글"], thumbnail_url: null, match: 83, slot: "추천" },
  { id: "maplem", name: "메이플스토리M", tags: ["MMORPG", "2D", "횡스크롤"], thumbnail_url: SAMPLE_IMAGE, match: 75, slot: "추천" },
  { id: "darkest", name: "다키스트 던전", tags: ["로그라이크", "2D"], thumbnail_url: "/static/img/없는-이미지.png", match: 47, slot: "탐색" },
];

function section(title, ...children) {
  return el("section", { class: "section" }, el("h2", { class: "section__title" }, title),
    el("div", { class: "card preview-stack" }, ...children));
}

function swatches() {
  const names = ["bg", "card", "surface", "text", "text-muted", "accent", "accent-strong", "danger"];
  return el("div", { class: "swatches" }, names.map((n) =>
    el("div", { class: "swatch" }, el("span", { class: `swatch__color swatch--${n}` }), `--${n}`)));
}

function chipDemo() {
  const chosen = new Set(["RPG"]);
  const box = el("div", { class: "chips" });
  const render = () => box.replaceChildren(...["RPG", "퍼즐", "로그라이크", "픽셀아트", "힐링"].map((name) =>
    tagChip(name, { selected: chosen.has(name), onClick: () => { chosen.has(name) ? chosen.delete(name) : chosen.add(name); render(); } })));
  render();
  return box;
}

function themeButtons() {
  const label = el("p", { class: "preview-note" });
  const update = () => { label.textContent = `지금 보이는 모드: ${effectiveTheme()}`; };
  document.addEventListener("gp:themechange", update);
  update();
  return [
    el("div", { class: "preview-row" },
      el("button", { type: "button", class: "btn btn--ghost", onclick: () => setTheme("light") }, "라이트"),
      el("button", { type: "button", class: "btn btn--ghost", onclick: () => setTheme("dark") }, "다크"),
      el("button", { type: "button", class: "btn btn--ghost", onclick: () => setTheme("system") }, "기기 설정")),
    label,
  ];
}

function apiDemo() {
  const result = el("p", { class: "preview-note" }, "버튼을 눌러 보세요");
  return [
    el("div", { class: "preview-row" },
      el("button", {
        type: "button", class: "btn btn--primary",
        onclick: async () => {
          const data = await api("/api/search/suggest?q=" + encodeURIComponent("메"));
          result.textContent = `성공: 자동완성 ${data.suggestions.map((s) => s.text).join(", ")}`;
        },
      }, "성공 호출"),
      el("button", {
        type: "button", class: "btn btn--danger",
        onclick: () => api("/api/games/nope").catch((e) => { result.textContent = `실패 (${e.status}): ${e.message}`; }),
      }, "오류 호출")),
    result,
  ];
}

const likeButton = el("button", { type: "button", class: "icon-btn is-active", "aria-pressed": "true" }, "👍 추천 3");

document.getElementById("app").append(
  topBar({ left: "theme", right: ["profile"], nickname: "게임왕" }),
  el("h1", { class: "section__title" }, "공통 부품 미리보기"),
  el("p", { class: "preview-note" }, "개발 확인용 페이지 (C5). 🌙 버튼이나 아래 버튼으로 모드를 바꿔 보세요."),

  section("화면 모드", ...themeButtons()),
  section("색", swatches()),
  section("글꼴 (나눔스퀘어라운드)",
    el("p", { class: "font-sample font-sample--400" }, "보통 400 — 별점이 말하지 않는 것 0123"),
    el("p", { class: "font-sample font-sample--700" }, "굵게 700 — 별점이 말하지 않는 것 0123"),
    el("p", { class: "font-sample font-sample--800" }, "아주 굵게 800 — 별점이 말하지 않는 것 0123")),
  section("상단바 (상세 / 마이페이지)",
    topBar({ left: "back", right: [likeButton, "home"] }),
    topBar({ left: "back", right: ["home"] })),
  section("버튼·입력",
    el("div", { class: "preview-row" },
      el("button", { type: "button", class: "btn btn--primary" }, "다음"),
      el("button", { type: "button", class: "btn btn--ghost" }, "이전"),
      el("button", { type: "button", class: "btn btn--danger" }, "내 데이터 삭제"),
      el("button", { type: "button", class: "btn btn--primary", disabled: true }, "비활성")),
    el("input", { class: "input", type: "search", placeholder: "게임 이름이나 키워드 (예: 로그라이크)", "aria-label": "검색" })),
  section("칩·부합도",
    chipDemo(),
    el("div", { class: "chips" }, tagChip("RPG", { removable: true, onClick: () => toast("키워드 지우기") }), tagChip("도트", { removable: true, onClick: () => toast("키워드 지우기") })),
    el("div", { class: "preview-row" }, matchBadge(83), matchBadge(0), matchBadge(null), matchBadge(75, { short: true }))),
  section("게임 카드 (썸네일 없음 / 있음 / 깨진 주소 → 대체 표시)",
    gameGrid(sampleGames, { maxTags: 2 }),
    gameGrid([], { empty: "추천할 게임이 아직 없어요" })),
  section("빈 값 대체 표시",
    emptyState("리뷰 분석 준비 중", "📊"),
    el("p", {}, "개발사: ", textOr(null)),
    el("p", {}, "설명: ", textOr("한 줄 설명이 있으면 그대로 보입니다."))),
  section("알림·API 호출",
    el("div", { class: "preview-row" },
      el("button", { type: "button", class: "btn btn--ghost", onclick: () => toast("저장했어요") }, "알림"),
      el("button", { type: "button", class: "btn btn--ghost", onclick: () => toast("문제가 생겼어요", "error") }, "오류 알림")),
    ...apiDemo()),
);

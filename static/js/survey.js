// 설문 페이지 (4.4 ①, 4.4.1 와이어프레임 01, 4.12 문항)
// 한 화면에 한 문항, 좌우 슬라이드. 필수 문항(1~5번)이 끝나면 오른쪽 위에 "건너뛰기" 등장.
// 처음 방문 때만: 이미 설문을 했으면 홈으로 (설정의 "설문 다시 하기"는 ?retake=1)
import { api, el, logo, pages, thumbnail, toast } from "/static/js/common.js";
import { featuredGames, isBlank, optionMatches } from "/static/js/survey_pick.js";

const CATEGORY_LABELS = { genre: "장르", subgenre: "세부 장르", view: "그래픽", play: "플레이", mood: "분위기", bm: "과금" };

const app = document.getElementById("app");
const retake = new URLSearchParams(location.search).has("retake");

let questions = [];
const answers = {};      // 문항 id → 고른 보기 값 목록 (지나온 문항만)
let index = 0;           // 지금 보이는 문항 (보이는 문항 목록 기준)
let sending = false;

// ---------- 문항 목록 ----------
function isShown(q) {
  return Object.entries(q.show_if || {}).every(([qid, values]) => (answers[qid] || []).some((v) => values.includes(v)));
}

const visible = () => questions.filter(isShown);
const lastRequiredId = () => [...questions].reverse().find((q) => q.required)?.id;

function pastRequired(q) {
  // 이 문항이 마지막 필수 문항보다 뒤에 있으면 건너뛰기 가능
  return questions.indexOf(q) > questions.findIndex((x) => x.id === lastRequiredId());
}

// ---------- 선택 ----------
function selection(q) {
  return answers[q.id] ? [...answers[q.id]] : [];
}

function toggle(q, option) {
  let chosen = selection(q);
  if (chosen.includes(option.value)) {
    chosen = chosen.filter((v) => v !== option.value);
  } else if (q.max === 1) {
    chosen = [option.value];
  } else if (option.exclusive) {
    chosen = [option.value];          // "딱히 없어요"를 고르면 나머지는 해제
  } else {
    const exclusive = new Set(q.options.filter((o) => o.exclusive).map((o) => o.value));
    chosen = chosen.filter((v) => !exclusive.has(v));
    if (q.max && chosen.length >= q.max) {
      toast(`최대 ${q.max}개까지 고를 수 있어요`);
      return;
    }
    chosen.push(option.value);
  }
  answers[q.id] = chosen;
  // 조건이 풀린 조건부 문항의 답은 지운다 (예: 2번을 바꾸면 2-1번 답 삭제)
  for (const other of questions) if (other.show_if && !isShown(other)) delete answers[other.id];
  render("none");
}

function canGoNext(q) {
  const n = selection(q).length;
  return n >= (q.min || 0) || (!q.required && n === 0);
}

// ---------- 그리기 ----------
function optionButton(q, option) {
  const selected = selection(q).includes(option.value);
  return el("button", {
    type: "button",
    class: `chip survey-option${selected ? " is-selected" : ""}${option.exclusive ? " survey-option--none" : ""}`,
    "aria-pressed": String(selected),
    onclick: () => toggle(q, option),
  }, option.label);
}

function gameOption(q, option, fromSearch = false) {
  const selected = selection(q).includes(option.value);
  return el("button", {
    type: "button",
    class: `survey-game${selected ? " is-selected" : ""}`,
    "aria-pressed": String(selected),
    onclick: () => pick(q, option, fromSearch),
  },
  el("span", { class: "survey-game__thumb" }, thumbnail({ name: option.label, thumbnail_url: option.thumbnail_url })),
  el("span", { class: "survey-game__name" }, option.label));
}

// ---------- 보기 줄이기 (C11-f) ----------
// 3·4번: 처음엔 대표 장르 5개 + 대표 키워드 5개(data/survey.json의 featured)만. 검색창으로 전체에서 찾기
// (이름 부분 일치 + 검색 동의어), "전체 목록 보기"로 펼치기(접힌 상태가 기본). 고른 것·검색해서 고른 것은 위 칩에 남김
// 5번: 3번 답에 맞는 대표작 3개 + 게임 이름 검색. 문항·점수 규칙은 그대로, 보여 주는 방식만 바꿈
const SEARCH_RESULTS = 8, GAME_RESULTS = 6;
const ui = {};   // 문항 id → { query, expanded, added: Set, refocus }

function uiOf(q) {
  ui[q.id] ||= { query: "", expanded: false, added: new Set(), refocus: false };
  return ui[q.id];
}

/** 고르기. 검색 결과에서 고른 것은 위 목록에 남기고, 다시 그린 뒤 검색창으로 돌아감 */
function pick(q, option, fromSearch) {
  if (fromSearch) {
    const state = uiOf(q);
    state.added.add(option.value);
    state.refocus = true;
  }
  toggle(q, option);
}

/**
 * 검색창 + 결과 칸. 글자를 칠 때는 결과 칸만 다시 그림 (입력 중 포커스 유지).
 * 결과가 그대로면 다시 그리지 않음: 한글 입력 중 결과를 누르면 조합이 끝나며 input이 한 번 더 와서
 * 누르는 사이 버튼이 바뀌어 눌림이 사라지던 문제 방지
 */
function searchBox(q, placeholder, find, draw, emptyText) {
  const state = uiOf(q);
  const input = el("input", {
    type: "search", class: "input survey-search", id: `survey-search-${q.id}`, placeholder,
    autocomplete: "off", enterkeyhint: "search", "aria-label": placeholder, maxlength: 30,
  });
  input.value = state.query;
  const box = el("div", { class: "survey-search__results", "aria-live": "polite" });
  let shownKey = null;
  const update = () => {
    const found = isBlank(state.query) ? null : find(state.query);
    const key = found === null ? "" : `:${found.map((o) => o.value).join("|")}`;
    if (key === shownKey) return;
    shownKey = key;
    if (found === null) box.replaceChildren();
    else if (!found.length) box.replaceChildren(el("p", { class: "survey-search__empty" }, emptyText));
    else box.replaceChildren(draw(found));
  };
  input.addEventListener("input", () => {
    state.query = input.value;
    update();
  });
  update();
  return [input, box];
}

function tagQuestionView(q, normal) {
  const state = uiOf(q);
  const chosen = new Set(selection(q));
  const keep = (o) => chosen.has(o.value) || state.added.has(o.value);
  const byValue = new Map(normal.map((o) => [o.value, o]));
  const listOf = (featured, isGenre) => [...new Set([
    ...(featured || []),
    ...normal.filter((o) => (o.category === "genre") === isGenre && keep(o)).map((o) => o.value),
  ])].map((v) => byValue.get(v)).filter(Boolean);

  const parts = [
    el("h3", { class: "survey-group" }, "장르"),
    el("div", { class: "chips" }, listOf(q.featured.genre, true).map((o) => optionButton(q, o))),
    el("h3", { class: "survey-group" }, "키워드"),
    el("div", { class: "chips" }, listOf(q.featured.keyword, false).map((o) => optionButton(q, o))),
    ...searchBox(q, "장르·키워드 찾기 (예: 로그라이크, 혼자)",
      (query) => normal.filter((o) => optionMatches(o, query)).slice(0, SEARCH_RESULTS),
      (found) => el("div", { class: "chips" }, found.map((o) => {
        const selected = chosen.has(o.value);
        return el("button", {
          type: "button", class: `chip survey-option${selected ? " is-selected" : ""}`, "aria-pressed": String(selected),
          onclick: () => pick(q, o, true),
        }, o.label, el("span", { class: "survey-search__cat" }, CATEGORY_LABELS[o.category] || ""));
      })),
      "맞는 장르·키워드가 없어요"),
    el("button", {
      type: "button", class: "btn btn--ghost survey-more", "aria-expanded": String(state.expanded),
      onclick: () => { state.expanded = !state.expanded; render("none"); },
    }, state.expanded ? "전체 목록 접기" : "전체 목록 보기"),
  ];
  if (state.expanded) {
    const groups = [...new Set(normal.map((o) => o.category))];
    parts.push(el("div", { class: "survey-all" }, groups.flatMap((category) => [
      el("h3", { class: "survey-group" }, CATEGORY_LABELS[category] || category),
      el("div", { class: "chips" }, normal.filter((o) => o.category === category).map((o) => optionButton(q, o))),
    ])));
  }
  return parts;
}

function gameQuestionView(q, normal) {
  const state = uiOf(q);
  const chosen = new Set(selection(q));
  const byValue = new Map(normal.map((o) => [o.value, o]));
  const shown = [...new Set([
    ...featuredGames(normal, answers.q3 || []).map((o) => o.value),
    ...normal.filter((o) => chosen.has(o.value) || state.added.has(o.value)).map((o) => o.value),
  ])];
  return [
    el("div", { class: "survey-games" }, shown.map((v) => gameOption(q, byValue.get(v)))),
    ...searchBox(q, "게임 이름으로 찾기",
      (query) => normal.filter((o) => optionMatches(o, query)).slice(0, GAME_RESULTS),
      (found) => el("div", { class: "survey-games" }, found.map((o) => gameOption(q, o, true))),
      "맞는 게임이 없어요"),
  ];
}

function optionsView(q) {
  const extra = q.options.filter((o) => o.exclusive);
  const normal = q.options.filter((o) => !o.exclusive);
  const parts = [];
  if (extra.length) parts.push(el("div", { class: "chips" }, extra.map((o) => optionButton(q, o))));

  if (normal.some((o) => o.genres && !o.category)) {          // 게임 보기 (5번)
    parts.push(...gameQuestionView(q, normal));
  } else if (q.featured && normal.some((o) => o.category)) {   // 대표 보기 + 검색 (3·4번, C11-f)
    parts.push(...tagQuestionView(q, normal));
  } else if (normal.some((o) => o.category)) {                 // 태그 보기: 종류별로 묶음
    const groups = [...new Set(normal.map((o) => o.category))];
    for (const category of groups) {
      if (groups.length > 1) parts.push(el("h3", { class: "survey-group" }, CATEGORY_LABELS[category] || category));
      parts.push(el("div", { class: "chips" }, normal.filter((o) => o.category === category).map((o) => optionButton(q, o))));
    }
  } else {
    parts.push(el("div", { class: "chips survey-choices" }, normal.map((o) => optionButton(q, o))));
  }
  return parts;
}

function countHint(q) {
  if (q.hint) return q.hint;
  if (q.max === 1) return "하나만 골라 주세요";
  return q.required ? "여러 개 골라도 돼요" : "여러 개 골라도 돼요 · 넘어가도 돼요";
}

function render(direction) {
  const list = visible();
  const q = list[index];
  const last = index === list.length - 1;
  const skip = pastRequired(q)
    ? el("button", { type: "button", class: "btn btn--ghost survey-skip", onclick: submit, disabled: sending }, "건너뛰기")
    : null;

  const card = el("section", { class: `card survey-card survey-card--${direction}`, "aria-labelledby": "survey-q" },
    el("div", { class: "survey-top" },
      el("span", { class: "survey-progress" }, `${index + 1} / ${list.length}`),
      skip),
    el("div", { class: "survey-bar", "aria-hidden": "true" }, el("span", { class: "survey-bar__fill" })),
    el("h1", { class: "survey-question", id: "survey-q" }, q.text),
    el("p", { class: "survey-hint" }, `${q.required ? "필수 · " : ""}${countHint(q)}`),
    el("div", { class: "survey-options" }, optionsView(q)),
    el("div", { class: "survey-nav" },
      el("button", { type: "button", class: "btn btn--ghost", onclick: prev, disabled: index === 0 }, "이전"),
      el("button", { type: "button", class: "btn btn--primary", onclick: next, disabled: !canGoNext(q) || sending },
        last ? "완료" : "다음")));
  card.querySelector(".survey-bar__fill").style.width = `${((index + 1) / list.length) * 100}%`;
  app.replaceChildren(el("header", { class: "survey-header" }, surveyLogo()), card);
  const state = ui[q.id];
  if (state?.refocus) {  // 검색 결과를 누른 뒤에도 이어서 찾을 수 있게 검색창으로
    state.refocus = false;
    const input = document.getElementById(`survey-search-${q.id}`);
    input?.focus({ preventScroll: true });
    input?.setSelectionRange(input.value.length, input.value.length);
  }
}

// 로고 (C11-b): 처음 설문은 끝내야 홈을 쓸 수 있어(홈이 설문으로 돌려보냄) 누를 수 없는 글자,
// 다시 하기(?retake=1)는 "그만둘까요?" 확인 뒤 홈으로 (답은 저장 안 됨)
function surveyLogo() {
  if (!retake) return logo({ isStatic: true });
  return logo({
    onClick: () => {
      if (window.confirm("설문을 그만둘까요? 지금 고른 답은 저장되지 않아요.")) location.assign("/");
    },
  });
}

// ---------- 이동·제출 ----------
function leave(q) {
  // 필수 문항은 비어 있어도 답(빈 목록)으로 남기고, 선택 문항은 비었으면 뺀다
  if (!answers[q.id]?.length && !q.required) delete answers[q.id];
  else answers[q.id] = selection(q);
}

function next() {
  const list = visible();
  const q = list[index];
  if (!canGoNext(q)) return;
  leave(q);
  if (index === list.length - 1) return submit();
  index += 1;
  render("next");
  window.scrollTo(0, 0);
}

function prev() {
  if (index === 0) return;
  index -= 1;
  render("prev");
  window.scrollTo(0, 0);
}

async function submit() {
  if (sending) return;
  const q = visible()[index];
  if (pastRequired(q) || index === visible().length - 1) leave(q);
  const payload = Object.fromEntries(Object.entries(answers).filter(([qid]) => {
    const question = questions.find((x) => x.id === qid);
    return question && isShown(question);
  }));
  sending = true;
  render("none");
  try {
    await api("/api/survey/answers", { method: "POST", body: { answers: payload } });
    location.replace(pages.home());
  } catch {
    sending = false;  // 오류 문구는 api()가 알림으로 보여 줌
    render("none");
  }
}

// ---------- 시작 ----------
async function start() {
  try {
    const me = await api("/api/users", { method: "POST" });
    if (me.survey_done && !retake) {
      location.replace(pages.home());
      return;
    }
    ({ questions } = await api("/api/survey/questions"));
    render("none");
  } catch (error) {
    app.replaceChildren(el("div", { class: "empty" }, error.message || "설문을 불러오지 못했어요"));
  }
}

start();

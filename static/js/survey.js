// 설문 페이지 (4.4 ①, 4.4.1 와이어프레임 01, 4.12 문항)
// 한 화면에 한 문항, 좌우 슬라이드. 필수 문항(1~5번)이 끝나면 오른쪽 위에 "건너뛰기" 등장.
// 처음 방문 때만: 이미 설문을 했으면 홈으로 (설정의 "설문 다시 하기"는 ?retake=1)
import { api, el, pages, thumbnail, toast } from "/static/js/common.js";

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

function gameOption(q, option) {
  const selected = selection(q).includes(option.value);
  return el("button", {
    type: "button",
    class: `survey-game${selected ? " is-selected" : ""}`,
    "aria-pressed": String(selected),
    onclick: () => toggle(q, option),
  },
  el("span", { class: "survey-game__thumb" }, thumbnail({ name: option.label, thumbnail_url: option.thumbnail_url })),
  el("span", { class: "survey-game__name" }, option.label));
}

/** 3번에서 고른 장르(세부 장르의 장르 포함) → 5번 게임 보기에서 그 장르 게임을 먼저 */
function likedGenres() {
  const q3 = questions.find((q) => q.id === "q3");
  if (!q3) return new Set();
  const picked = q3.options.filter((o) => (answers.q3 || []).includes(o.value));
  return new Set(picked.flatMap((o) => o.genres || []));
}

function optionsView(q) {
  const extra = q.options.filter((o) => o.exclusive);
  const normal = q.options.filter((o) => !o.exclusive);
  const parts = [];
  if (extra.length) parts.push(el("div", { class: "chips" }, extra.map((o) => optionButton(q, o))));

  if (normal.some((o) => o.genres && !o.category)) {          // 게임 보기 (5번)
    const genres = likedGenres();
    const sorted = [...normal].sort((a, b) =>
      Number(b.genres.some((g) => genres.has(g))) - Number(a.genres.some((g) => genres.has(g))));
    parts.push(el("div", { class: "survey-games" }, sorted.map((o) => gameOption(q, o))));
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
  app.replaceChildren(card);
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

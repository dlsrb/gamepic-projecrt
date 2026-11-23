// 마이페이지 (4.4 ⑤, 4.4.1 와이어프레임 07)
// 상단바(← / 홈) → 프로필 원 → 닉네임 → 배지 줄(처음엔 빈 자리) → 취향 분석(육각형 레이더 차트, SVG)
import { api, bottomNav, el, emptyState, tagChip, toast, topBar } from "/static/js/common.js";

const AXIS_LABELS = { genre: "장르", subgenre: "세부 장르", view: "그래픽", play: "플레이", mood: "분위기", bm: "과금" };
const SCORE_MIN = -5, SCORE_MAX = 5;   // 태그 점수 범위 (reco_config와 같음)
const BADGE_SLOTS = 4;
const SVG_NS = "http://www.w3.org/2000/svg";
const app = document.getElementById("app");

function section(title, ...children) {
  return el("section", { class: "section" }, el("h2", { class: "section__title" }, title), ...children);
}

function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  for (const child of children) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}

// ---------- 닉네임 ----------
function nicknameBlock(me) {
  const box = el("div", { class: "my-name" });
  const avatar = el("div", { class: "my-avatar", "aria-hidden": "true" });
  const show = () => {
    avatar.textContent = me.nickname ? me.nickname.slice(0, 1) : "나";
    box.replaceChildren(
      el("p", { class: "my-name__text" }, me.nickname || "닉네임을 정해 주세요"),
      el("button", { type: "button", class: "btn btn--ghost", onclick: edit }, me.nickname ? "닉네임 바꾸기" : "닉네임 정하기"));
  };
  const edit = () => {
    const input = el("input", {
      class: "input", maxlength: "12", value: me.nickname || "", "aria-label": "새 닉네임", autocomplete: "off",
      "aria-describedby": "nick-rule",
    });
    const save = async (event) => {
      event.preventDefault();
      const result = await api("/api/me", { method: "PATCH", body: { nickname: input.value } }).catch(() => null);
      if (!result) return;  // 규칙에 안 맞으면 서버 문구가 알림으로 뜸
      me.nickname = result.nickname;
      toast("닉네임을 바꿨어요");
      show();
    };
    box.replaceChildren(el("form", { class: "my-name__form", onsubmit: save },
      input,
      el("p", { class: "my-name__rule", id: "nick-rule" }, "한글 2~10자 · 영어 4~12자 · 한글+영어 2~12자 (숫자 포함 가능)"),
      el("div", { class: "my-name__buttons" },
        el("button", { type: "button", class: "btn btn--ghost", onclick: show }, "취소"),
        el("button", { type: "submit", class: "btn btn--primary" }, "저장"))));
    input.focus();
  };
  show();
  return el("div", { class: "my-profile" }, avatar, box);
}

function badges() {
  return section("배지",
    el("div", { class: "my-badges" }, Array.from({ length: BADGE_SLOTS }, () => el("span", { class: "my-badge", "aria-hidden": "true" }))),
    el("p", { class: "placeholder-text my-badges__note" }, "배지 준비 중이에요"));
}

// ---------- 취향 레이더 차트 ----------
function radar(axes) {
  const size = 300, center = size / 2, radius = 100;
  const angle = (i) => (Math.PI * 2 * i) / axes.length - Math.PI / 2;
  const point = (i, ratio) => [center + Math.cos(angle(i)) * radius * ratio, center + Math.sin(angle(i)) * radius * ratio];
  const ratioOf = (v) => (Math.max(SCORE_MIN, Math.min(SCORE_MAX, v)) - SCORE_MIN) / (SCORE_MAX - SCORE_MIN);
  const polygon = (ratios) => ratios.map((r, i) => point(i, r).map((n) => n.toFixed(1)).join(",")).join(" ");

  const grid = [0.25, 0.5, 0.75, 1].map((level) => svg("polygon", {
    class: level === 0.5 ? "radar__zero" : "radar__grid", points: polygon(axes.map(() => level)),
  }));
  const spokes = axes.map((_, i) => {
    const [x, y] = point(i, 1);
    return svg("line", { class: "radar__grid", x1: center, y1: center, x2: x.toFixed(1), y2: y.toFixed(1) });
  });
  const labels = axes.map((a, i) => {
    const [x, y] = point(i, 1.22);
    return svg("text", { class: "radar__label", x: x.toFixed(1), y: y.toFixed(1), "text-anchor": "middle", "dominant-baseline": "middle" },
      AXIS_LABELS[a.category] || a.category);
  });
  const shape = svg("polygon", { class: "radar__shape", points: polygon(axes.map((a) => ratioOf(a.average))) });
  const dots = axes.map((a, i) => {
    const [x, y] = point(i, ratioOf(a.average));
    return svg("circle", { class: "radar__dot", cx: x.toFixed(1), cy: y.toFixed(1), r: 4 },
      svg("title", {}, `${AXIS_LABELS[a.category]} 평균 ${a.average}`));
  });
  const summary = axes.map((a) => `${AXIS_LABELS[a.category]} ${a.average}`).join(", ");
  return svg("svg", { class: "radar", viewBox: `0 0 ${size} ${size}`, role: "img", "aria-label": `취향 레이더 차트: ${summary}` },
    ...grid, ...spokes, shape, ...dots, ...labels);
}

function tasteSection(taste) {
  if (!taste.axes.some((a) => a.count > 0)) return section("취향 분석", emptyState("설문을 하면 취향 분석이 보여요"));
  return section("취향 분석",
    el("div", { class: "card my-taste" },
      radar(taste.axes),
      el("p", { class: "my-taste__note" }, "가운데 진한 선이 0점, 바깥으로 갈수록 좋아하는 쪽 (종류별 점수 평균, −5~+5)"),
      el("h3", { class: "detail-sub" }, "좋아하는 키워드"),
      taste.top.length ? el("div", { class: "chips" }, taste.top.map((t) => tagChip(`${t.name} +${t.score}`))) : el("p", { class: "placeholder-text" }, "아직 없어요"),
      el("h3", { class: "detail-sub" }, "피하는 키워드"),
      taste.bottom.length ? el("div", { class: "chips" }, taste.bottom.map((t) => tagChip(`${t.name} ${t.score}`))) : el("p", { class: "placeholder-text" }, "아직 없어요")));
}

async function start() {
  const [me, taste] = await Promise.all([api("/api/me"), api("/api/me/taste")]);
  app.append(
    topBar({ left: "back" }),
    nicknameBlock(me),
    badges(),
    tasteSection(taste),
  );
  bottomNav();
}

start();

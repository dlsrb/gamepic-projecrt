// 게임 상세 (4.4 ④, 4.4.1 와이어프레임 05·06)
// 상단바(← / 👍추천·홈) → 게임 카드(누르면 스토어, 아래 부합도) + 이름·순위·키워드 4개 → 영상 → 게임 소개
// → 별점·리뷰 → 댓글(C8-c) → 비슷한 게임 → 리뷰 데이터 분석(맨 아래) / 하단 메뉴(강조 없음)
import {
  api, bottomNav, el, emptyState, gameGrid, matchBadge, pages, tagChip, textOr, thumbnail, toast, topBar,
} from "/static/js/common.js";
import { missingStoreMessage, storeUrl } from "/static/js/store.js";

const COMMENT_MAX = 300, COMMENT_LINES = 10, COMMENT_PAGE = 20;

const SENTIMENTS = [
  { key: "pos", label: "긍정" }, { key: "neg", label: "부정" }, { key: "neu", label: "중립" }, { key: "irr", label: "관계없음" },
];
const app = document.getElementById("app");
const gameId = new URLSearchParams(location.search).get("id") || "";

function section(title, ...children) {
  return el("section", { class: "section" }, el("h2", { class: "section__title" }, title), ...children);
}

// ---------- 👍 추천 ----------
function likeButton(game) {
  const button = el("button", { type: "button", class: "icon-btn" });
  const render = () => {
    button.textContent = `👍 추천 ${game.like_count}`;
    button.classList.toggle("is-active", game.liked);
    button.setAttribute("aria-pressed", String(game.liked));
    button.setAttribute("aria-label", game.liked ? `추천 취소 (지금 ${game.like_count})` : `추천하기 (지금 ${game.like_count})`);
  };
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const result = game.liked
        ? await api(`/api/reactions/like/${encodeURIComponent(game.id)}`, { method: "DELETE" })
        : await api("/api/reactions", { method: "POST", body: { game_id: game.id, type: "like" } });
      Object.assign(game, { like_count: result.like_count, liked: result.liked });
      render();
    } finally {
      button.disabled = false;
    }
  });
  render();
  return button;
}

// ---------- 스토어 이동 (기기별 고르기는 store.js) ----------
function openStore(game) {
  const url = storeUrl(game, navigator);
  if (!url) {
    toast(missingStoreMessage(navigator));
    return;
  }
  window.open(url, "_blank", "noopener");
  // 스토어 이동 = 취향 점수 +0.5 (같은 게임은 하루 1회만 반영 — 서버에서 처리)
  api("/api/reactions", { method: "POST", body: { game_id: game.id, type: "store" }, silent: true }).catch(() => {});
}

// ---------- 위쪽: 카드 + 이름·순위·키워드 ----------
function head(game) {
  const keywords = [...game.tags.genre, ...game.tags.subgenre, ...game.tags.view, ...game.tags.mood, ...game.tags.play].slice(0, 4);
  return el("div", { class: "detail-head" },
    el("div", { class: "detail-card" },
      el("button", { type: "button", class: "detail-card__thumb", onclick: () => openStore(game), "aria-label": `${game.name} 스토어로 이동` },
        thumbnail(game)),
      el("span", { class: "detail-card__hint" }, "누르면 스토어로"),
      matchBadge(game.match)),
    el("div", { class: "detail-info" },
      el("h1", { class: "detail-name" }, game.name),
      el("span", { class: "chip detail-rank" }, game.rank ? `추천 순위 ${game.rank}위` : "순위 없음"),
      el("div", { class: "chips" }, keywords.map((k) => tagChip(k))),
      game.indie ? el("span", { class: "chip" }, "인디") : null,
      game.reasons.length ? el("p", { class: "detail-reasons" }, `내 취향과 맞는 점: ${game.reasons.join(", ")}`) : null));
}

function videoSection(game) {
  if (!game.video_url) return section("영상", emptyState("영상 준비 중", "🎬"));
  return section("영상", el("a", { class: "btn btn--ghost btn--block", href: game.video_url, target: "_blank", rel: "noopener" }, "▶ 영상 보기"));
}

function introSection(game) {
  return section("게임 소개", el("div", { class: "card detail-intro" },
    el("p", { class: "detail-desc" }, textOr(game.description)),
    el("dl", { class: "detail-facts" },
      el("dt", {}, "개발사"), el("dd", {}, textOr(game.developer)),
      el("dt", {}, "출시일"), el("dd", {}, textOr(game.release_date)),
      el("dt", {}, "규모"), el("dd", {}, textOr(game.tier)),
      el("dt", {}, "다운로드"), el("dd", {}, textOr(formatInstalls(game.installs))))));
}

/** 10000000 → "1,000만+" (Google Play 설치 수) */
function formatInstalls(n) {
  if (!n) return null;
  if (n >= 1e8) return `${(n / 1e8).toLocaleString()}억+`;
  if (n >= 1e4) return `${(n / 1e4).toLocaleString()}만+`;
  return `${n.toLocaleString()}+`;
}

// ---------- 별점 ----------
function ratingSection(game, reviews) {
  const parts = [];
  if (game.store_rating) {
    parts.push(el("p", { class: "detail-rating" }, `★ ${Number(game.store_rating).toFixed(1)}`,
      game.store_rating_count ? el("span", { class: "placeholder-text" }, ` (${game.store_rating_count.toLocaleString()}명)`) : null));
  }
  const total = reviews.stars.reduce((sum, s) => sum + s.review_count, 0);
  if (total > 0) {
    const byStar = Object.fromEntries(reviews.stars.map((s) => [s.star, s.review_count]));
    parts.push(el("div", { class: "star-bars" }, [5, 4, 3, 2, 1].map((star) => {
      const fill = el("span", { class: "bar__fill" });
      fill.style.width = `${((byStar[star] || 0) / total) * 100}%`;
      return el("div", { class: "star-bars__row" },
        el("span", {}, `${star}점`), el("span", { class: "bar" }, fill), el("span", { class: "star-bars__n" }, `${byStar[star] || 0}`));
    })));
  }
  if (!parts.length) return section("별점", emptyState("별점 정보 준비 중", "⭐"));
  return section("별점", el("div", { class: "card" }, parts));
}

// ---------- 댓글 (C8-c) ----------
/** 서버 시각("2026-11-09 06:30:00", UTC) → "방금" / "3분 전" / "2시간 전" / "11/9" */
function timeAgo(utc) {
  const time = new Date(`${String(utc).replace(" ", "T")}Z`);
  const seconds = (Date.now() - time.getTime()) / 1000;
  if (Number.isNaN(seconds)) return "";
  if (seconds < 60) return "방금";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분 전`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}시간 전`;
  return `${time.getMonth() + 1}/${time.getDate()}`;
}

/** 서버와 같은 기준으로 미리 확인 (최종 검사는 서버). 문제가 있으면 안내 문구, 없으면 null */
function commentProblem(text) {
  const body = text.trim();
  if (!body) return "댓글 내용을 입력해 주세요.";
  if (body.length > COMMENT_MAX) return `댓글은 ${COMMENT_MAX}자까지 쓸 수 있어요.`;
  if (body.split("\n").length > COMMENT_LINES) return `댓글은 ${COMMENT_LINES}줄까지 쓸 수 있어요.`;
  return null;
}

/** 입력창 + 글자 수 + 버튼 (새 댓글·고치기 공통) */
function commentEditor({ value = "", submitLabel, onSubmit, onCancel = null }) {
  const id = `comment-${Math.random().toString(36).slice(2, 8)}`;
  const input = el("textarea", { id, class: "input comment-input", rows: "3", maxlength: String(COMMENT_MAX),
    placeholder: "이 게임에 대한 생각을 남겨 주세요", "aria-label": "댓글 내용" });
  input.value = value;
  const count = el("span", { class: "comment-count", "aria-live": "polite" });
  const submit = el("button", { type: "submit", class: "btn btn--primary btn--small" }, submitLabel);
  const update = () => {
    count.textContent = `${input.value.trim().length}/${COMMENT_MAX}`;
    submit.disabled = Boolean(commentProblem(input.value));
  };
  input.addEventListener("input", update);
  update();
  const form = el("form", { class: "comment-form" }, input,
    el("div", { class: "comment-form__bar" }, count,
      el("div", { class: "comment-form__buttons" },
        onCancel ? el("button", { type: "button", class: "btn btn--ghost btn--small", onclick: onCancel }, "취소") : null,
        submit)));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const problem = commentProblem(input.value);
    if (problem) {
      toast(problem, "error");
      return;
    }
    submit.disabled = true;
    try {
      await onSubmit(input.value);
    } catch {
      // api()가 서버 문구로 알림을 띄움
    } finally {
      update();
    }
  });
  return { form, input };
}

/** 댓글 👍 추천 / 👎 비추천 (C9). 누르면 숫자를 바로 바꾸고 서버 응답으로 맞춤, 실패하면 되돌림 */
function voteButtons(c) {
  const state = { likes: c.likes, dislikes: c.dislikes, my_vote: c.my_vote };
  const up = el("button", { type: "button", class: "vote-btn" });
  const down = el("button", { type: "button", class: "vote-btn" });
  const render = () => {
    up.textContent = `👍 ${state.likes}`;
    down.textContent = `👎 ${state.dislikes}`;
    up.setAttribute("aria-pressed", String(state.my_vote === 1));
    down.setAttribute("aria-pressed", String(state.my_vote === -1));
    up.setAttribute("aria-label", `추천 ${state.likes}`);
    down.setAttribute("aria-label", `비추천 ${state.dislikes}`);
  };
  if (c.mine) {
    for (const b of [up, down]) {
      b.disabled = true;
      b.title = "내 댓글에는 누를 수 없어요";
    }
  } else {
    const press = async (value) => {
      const before = { ...state };
      const next = state.my_vote === value ? 0 : value;   // 다시 누르면 취소
      if (state.my_vote === 1) state.likes -= 1;
      if (state.my_vote === -1) state.dislikes -= 1;
      if (next === 1) state.likes += 1;
      if (next === -1) state.dislikes += 1;
      state.my_vote = next;
      render();
      up.disabled = down.disabled = true;                   // 요청 중 연타 방지
      try {
        Object.assign(state, await api(`/api/comments/${c.id}/vote`, { method: "PUT", body: { value: next } }));
      } catch {
        Object.assign(state, before);                       // api()가 서버 문구로 알림
      } finally {
        up.disabled = down.disabled = false;
        render();
      }
    };
    up.addEventListener("click", () => press(1));
    down.addEventListener("click", () => press(-1));
  }
  render();
  return el("div", { class: "comment-votes", role: "group", "aria-label": "댓글 추천" }, up, down);
}

// C9-b: 접힌 상태 = 추천순 상위 3개 / 펼친 상태 = 정렬 칩 3개 + 20개씩 더 보기 + 접기
const COMMENT_PREVIEW = 3;
const COMMENT_SORTS = [{ key: "latest", label: "최신순" }, { key: "oldest", label: "등록순" }, { key: "top", label: "추천순" }];
const SORT_STORAGE_KEY = "gp_comment_sort";

function savedSort() {
  try {
    const value = localStorage.getItem(SORT_STORAGE_KEY);
    return COMMENT_SORTS.some((s) => s.key === value) ? value : "top";
  } catch {
    return "top";
  }
}

function saveSort(value) {
  try {
    localStorage.setItem(SORT_STORAGE_KEY, value);
  } catch {
    // 저장 못 해도 이번 화면에서는 그대로 동작
  }
}

function commentsSection(game) {
  const title = el("h2", { class: "section__title comment-title", tabindex: "-1" }, "댓글");
  const list = el("ul", { class: "comment-list" });
  const empty = el("p", { class: "placeholder-text comment-empty" }, "첫 댓글을 남겨 보세요");
  const more = el("button", { type: "button", class: "btn btn--ghost btn--block btn--small", hidden: true }, "댓글 더 보기");
  const expand = el("button", { type: "button", class: "btn btn--ghost btn--block btn--small", hidden: true });
  const collapse = el("button", { type: "button", class: "btn btn--ghost btn--block btn--small", hidden: true }, "접기");
  const sortChips = COMMENT_SORTS.map((s) => el("button", { type: "button", class: "chip comment-sort__chip",
    "data-sort": s.key, onclick: () => changeSort(s.key) }, s.label));
  const sortBar = el("div", { class: "chips comment-sort", role: "group", "aria-label": "댓글 정렬", hidden: true }, sortChips);
  let total = 0, cursor = null, expanded = false, sort = savedSort(), request = 0;
  const base = `/api/games/${encodeURIComponent(game.id)}/comments`;

  const setTotal = (n) => {
    total = n;
    title.textContent = `댓글 ${total}`;
    empty.hidden = total > 0;
    expand.textContent = `댓글 ${total}개 전체 보기`;
    expand.hidden = expanded || total <= COMMENT_PREVIEW;
  };

  const renderSort = () => {
    for (const chip of sortChips) {
      const on = chip.dataset.sort === sort;
      chip.classList.toggle("is-selected", on);
      chip.setAttribute("aria-pressed", String(on));
    }
  };

  const item = (c) => {
    const body = el("p", { class: "comment-body" }, c.body);
    const meta = el("div", { class: "comment-meta" },
      el("span", { class: "comment-nick" }, c.nickname),
      el("span", { class: "comment-time" }, timeAgo(c.created_at)),
      c.edited ? el("span", { class: "comment-edited" }, "(수정됨)") : null);
    const foot = el("div", { class: "comment-foot" }, voteButtons(c));
    const li = el("li", { class: "comment" }, meta, body, foot);
    if (c.mine) {
      const actions = el("div", { class: "comment-actions" },
        el("button", { type: "button", class: "link-btn", onclick: () => startEdit() }, "고치기"),
        el("button", { type: "button", class: "link-btn link-btn--danger", onclick: () => remove() }, "지우기"));
      foot.append(actions);
      const startEdit = () => {
        const editor = commentEditor({
          value: c.body, submitLabel: "저장",
          onSubmit: async (text) => {
            const saved = await api(`/api/comments/${c.id}`, { method: "PATCH", body: { body: text } });
            li.replaceWith(item(saved));
          },
          onCancel: () => {
            editor.form.replaceWith(body);
            actions.hidden = false;
          },
        });
        body.replaceWith(editor.form);
        actions.hidden = true;
        editor.input.focus();
      };
      const remove = async () => {
        if (!confirm("이 댓글을 지울까요? 되돌릴 수 없어요.")) return;
        await api(`/api/comments/${c.id}`, { method: "DELETE" });
        li.remove();
        setTotal(total - 1);
        if (!expanded) reload();   // 접힌 상태면 빈자리를 다음 순위로 채움
      };
    }
    return li;
  };

  /** fresh = 처음부터 (목록 비우고). 정렬을 빨리 바꿔도 마지막 요청 결과만 씀 */
  const load = async (fresh = false) => {
    const mine = ++request;
    more.disabled = true;
    try {
      const params = new URLSearchParams({ sort: expanded ? sort : "top", limit: String(expanded ? COMMENT_PAGE : COMMENT_PREVIEW) });
      if (!fresh && cursor) params.set("cursor", cursor);
      const page = await api(`${base}?${params}`);
      if (mine !== request) return;
      if (fresh) list.replaceChildren();
      list.append(...page.comments.map(item));
      cursor = page.next_cursor;
      setTotal(page.total);
      more.hidden = !expanded || !page.has_more;
    } finally {
      if (mine === request) more.disabled = false;
    }
  };
  const reload = () => {
    cursor = null;
    return load(true);
  };
  more.addEventListener("click", () => load());

  const setExpanded = (value) => {
    expanded = value;
    sortBar.hidden = collapse.hidden = !expanded;
    more.hidden = true;
    renderSort();
    setTotal(total);
  };
  expand.addEventListener("click", () => {
    setExpanded(true);
    reload();
  });
  collapse.addEventListener("click", () => {
    setExpanded(false);
    reload();
    title.scrollIntoView({ behavior: "smooth", block: "start" });
    title.focus({ preventScroll: true });
  });
  const changeSort = (key) => {
    if (key === sort) return;
    sort = key;
    saveSort(key);
    renderSort();
    reload();
  };

  const writer = commentEditor({
    submitLabel: "등록",
    onSubmit: async (text) => {
      const created = await api(base, { method: "POST", body: { body: text } });
      writer.input.value = "";
      list.prepend(item(created));   // 방금 쓴 댓글은 정렬과 상관없이 맨 위에 (새로고침하면 원래 규칙)
      setTotal(total + 1);
    },
  });

  setTotal(0);
  renderSort();
  load(true);
  return el("section", { class: "section", "aria-label": "댓글" },
    title, writer.form, empty, sortBar, list, more, expand, collapse);
}

// ---------- 리뷰 데이터 분석 (핵심 가치 ②) ----------
function sentimentBar(row) {
  const total = SENTIMENTS.reduce((sum, s) => sum + row[s.key], 0) || 1;
  return el("span", { class: "stack-bar" }, SENTIMENTS.map((s) => {
    const part = el("span", { class: `stack-bar__part stack-bar__part--${s.key}`, title: `${s.label} ${row[s.key]}문장` });
    part.style.width = `${(row[s.key] / total) * 100}%`;
    return part;
  }));
}

function analysisSection(reviews) {
  if (!reviews.ready) return section("리뷰 데이터 분석", emptyState("리뷰 분석 준비 중", "📊"));
  const legend = el("div", { class: "chips stack-legend" }, SENTIMENTS.map((s) =>
    el("span", { class: "chip" }, el("span", { class: `dot dot--${s.key}`, "aria-hidden": "true" }), s.label)));
  const stars = el("div", { class: "sentiment-rows" }, [...reviews.stars].sort((a, b) => b.star - a.star).map((row) =>
    el("div", { class: "sentiment-row" }, el("span", {}, `${row.star}점`), sentimentBar(row))));
  const topics = reviews.topics.length
    ? el("ul", { class: "topic-list" }, reviews.topics.map((t) => {
      const fill = el("span", { class: "bar__fill bar__fill--neg" });
      fill.style.width = `${Math.min(100, t.neg_ratio * 100)}%`;
      return el("li", {}, el("span", { class: "topic-list__name" }, t.topic), el("span", { class: "bar" }, fill),
        el("span", { class: "topic-list__n" }, `${Math.round(t.neg_ratio * 100)}%`));
    }))
    : el("p", { class: "placeholder-text" }, "주제별 불만 정보 준비 중");
  return section("리뷰 데이터 분석",
    el("div", { class: "card preview-stack detail-analysis" },
      reviews.source === "temp" ? el("span", { class: "chip" }, "임시 분석 (키워드 기준)") : null,
      el("h3", { class: "detail-sub" }, "별점별 문장 감정"), legend, stars,
      el("h3", { class: "detail-sub" }, "주제별 불만 비율"), topics));
}

async function start() {
  try {
    const game = await api(`/api/games/${encodeURIComponent(gameId)}`, { silent: true });
    document.title = `게임픽 · ${game.name}`;
    const [similar, reviews] = await Promise.all([
      api(`/api/games/${encodeURIComponent(gameId)}/similar?limit=6`),
      api(`/api/games/${encodeURIComponent(gameId)}/reviews`),
    ]);
    app.append(
      topBar({ left: "back", right: [likeButton(game), "home"] }),
      head(game),
      videoSection(game),
      introSection(game),
      ratingSection(game, reviews),
      commentsSection(game),
      section("비슷한 게임", gameGrid(similar.games, { empty: "비슷한 게임을 찾지 못했어요" })),
      analysisSection(reviews),
    );
  } catch (error) {
    app.append(topBar({ left: "back", right: ["home"] }),
      emptyState(error.status === 404 || error.status === 422 ? "게임을 찾을 수 없어요" : error.message, "🔍"));
  }
  bottomNav();
}

start();

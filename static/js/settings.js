// 설정 (4.4 ⑥). 와이어프레임 없음 → 상단바 + 묶음별 둥근 목록 (4.4.1, Claude 제안)
// 화면 / 취향 / 개인정보 / 정보
import { api, bottomNav, el, getTheme, pages, setTheme, tagChip, toast, topBar } from "/static/js/common.js";

const VERSION = "0.1.0";
const CATEGORY_LABELS = { genre: "장르", subgenre: "세부 장르", view: "그래픽", play: "플레이", mood: "분위기", bm: "과금" };
const app = document.getElementById("app");

function group(title, ...rows) {
  return el("section", { class: "section" }, el("h2", { class: "section__title" }, title),
    el("div", { class: "card settings-list" }, ...rows));
}

/** 목록 한 줄. 누르면 아래로 펼쳐지는 내용(panel)이 있으면 펼침 버튼, 아니면 onClick/href */
function row(label, { onClick, href, panel, danger = false, detail, detailId } = {}) {
  const cls = `settings-row${danger ? " settings-row--danger" : ""}`;
  const content = [el("span", { class: "settings-row__label" }, label),
    detail ? el("span", { class: "settings-row__detail", id: detailId }, detail) : null];
  if (href) return el("a", { class: cls, href, ...(href.startsWith("/api/") ? { download: "" } : {}) }, ...content, el("span", { "aria-hidden": "true" }, "›"));
  if (!panel) return el("button", { type: "button", class: cls, onclick: onClick }, ...content);
  const body = el("div", { class: "settings-panel", hidden: true });
  const toggle = el("button", { type: "button", class: cls, "aria-expanded": "false" }, ...content, el("span", { "aria-hidden": "true" }, "⌄"));
  toggle.addEventListener("click", () => {
    const open = body.hidden;
    body.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    if (open && !body.childElementCount) body.append(...[].concat(panel()));
  });
  return el("div", {}, toggle, body);
}

// ---------- 화면 ----------
function themeRow() {
  const options = [["light", "라이트"], ["dark", "다크"], ["system", "기기 설정 따라가기"]];
  const box = el("div", { class: "chips settings-theme", role: "radiogroup", "aria-label": "화면 모드" });
  const render = () => box.replaceChildren(...options.map(([value, label]) =>
    el("button", {
      type: "button", role: "radio", class: `chip${getTheme() === value ? " is-selected" : ""}`,
      "aria-checked": String(getTheme() === value), onclick: () => { setTheme(value); render(); },
    }, label)));
  render();
  return el("div", { class: "settings-row settings-row--static settings-row--stack" },
    el("span", { class: "settings-row__label" }, "화면 모드"), box);
}

// ---------- 비선호 태그 관리 ----------
function dislikePanel(me) {
  const chosen = new Set(me.disliked_tags);
  const box = el("div", { class: "settings-dislikes" }, el("p", { class: "placeholder-text" }, "불러오는 중…"));
  api("/api/tags").then(({ tags }) => {
    const render = () => box.replaceChildren(
      el("p", { class: "settings-note" }, "고른 키워드가 들어간 게임은 추천하지 않아요."),
      ...Object.entries(tags).flatMap(([category, list]) => [
        el("h3", { class: "survey-group" }, CATEGORY_LABELS[category] || category),
        el("div", { class: "chips" }, list.map((t) => {
          const feature = `${category}:${t.name}`;
          return tagChip(t.name, {
            selected: chosen.has(feature),
            onClick: () => { chosen.has(feature) ? chosen.delete(feature) : chosen.add(feature); render(); },
          });
        })),
      ]),
      el("button", {
        type: "button", class: "btn btn--primary btn--block", onclick: async () => {
          await api("/api/me/disliked-tags", { method: "PUT", body: { tags: [...chosen] } });
          me.disliked_tags = [...chosen];
          document.getElementById("dislike-count").textContent = `${chosen.size}개`;
          toast(`비선호 키워드 ${chosen.size}개를 저장했어요`);
        },
      }, "저장"));
    render();
  });
  return box;
}

// ---------- 정보 ----------
const INFO = {
  privacy: [
    "게임픽은 회원가입 없이 쓰는 서비스예요. 브라우저마다 임시 ID(쿠키)를 하나 만들고, 닉네임만 따로 받아요.",
    "저장하는 것: 임시 ID, 닉네임, 설문 응답, 취향 점수, 따봉·스토어 이동·관심 없음·조회 기록, 내가 쓴 댓글.",
    "댓글은 닉네임(없으면 '익명 사용자')과 함께 다른 사람에게 공개돼요. 내 댓글은 언제든 고치거나 지울 수 있어요.",
    "받지 않는 것: 이메일, 전화번호, 실명. 검색어는 누가 검색했는지 남기지 않아요.",
    "'내 데이터 내려받기'로 저장된 내용을 볼 수 있고, '내 데이터 삭제'로 모두 지울 수 있어요.",
  ],
  terms: [
    "게임픽은 수업 프로젝트로 만든 게임 추천 서비스예요. 추천과 리뷰 분석은 참고용이에요.",
    "게임 정보와 리뷰 분석은 공개된 자료를 바탕으로 하며, 실제와 다를 수 있어요.",
  ],
  licenses: [
    "나눔스퀘어라운드 — 네이버, SIL Open Font License 1.1",
    "FastAPI, Starlette, Pydantic — MIT License",
    "Uvicorn — BSD 3-Clause License",
    "python-dotenv — BSD 3-Clause License",
  ],
};
const infoPanel = (lines) => () => el("ul", { class: "settings-info" }, lines.map((line) => el("li", {}, line)));

async function start() {
  const me = await api("/api/me");
  app.append(
    topBar({ left: "back" }),
    el("h1", { class: "section__title settings-title" }, "설정"),
    group("화면", themeRow()),
    group("취향",
      row("설문 다시 하기", { href: `${pages.survey()}?retake=1` }),
      row("비선호 키워드 관리", { panel: () => dislikePanel(me), detail: `${me.disliked_tags.length}개`, detailId: "dislike-count" }),
      row("추천 기록 초기화", {
        onClick: async () => {
          if (!confirm("따봉·스토어 이동·관심 없음 기록을 지우고 설문 결과로 추천을 되돌릴까요? (최근 본 게임은 남아요)")) return;
          const { deleted_reactions: n } = await api("/api/me/reset", { method: "POST" });
          toast(`추천 기록 ${n}개를 지웠어요`);
        },
      })),
    group("개인정보",
      row("내 데이터 내려받기", { href: "/api/me/export" }),
      row("내 데이터 삭제", {
        danger: true,
        onClick: async () => {
          if (!confirm("닉네임·설문·추천 기록·댓글이 모두 지워지고 되돌릴 수 없어요. 삭제할까요?")) return;
          await api("/api/me", { method: "DELETE" });
          location.replace(pages.survey());
        },
      })),
    group("정보",
      row("개인정보 처리방침", { panel: infoPanel(INFO.privacy) }),
      row("이용약관", { panel: infoPanel(INFO.terms) }),
      row("오픈소스 라이선스", { panel: infoPanel(INFO.licenses) }),
      el("div", { class: "settings-row settings-row--static" },
        el("span", { class: "settings-row__label" }, "버전"), el("span", { class: "settings-row__detail" }, VERSION))),
  );
  bottomNav("settings");
}

start();

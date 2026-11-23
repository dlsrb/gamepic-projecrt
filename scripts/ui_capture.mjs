// 화면 캡처 (PROJECT.md 4.11 C12-f 전후 캡처용). 설치 없이 Chrome(headless) + Node 내장 WebSocket으로 찍는다.
// 로컬 서버(python app/main.py)를 먼저 켜고:
//   node scripts/ui_capture.mjs <저장 폴더> <접두어>          예: node scripts/ui_capture.mjs docs/ui_c12 before
// 375px(라이트·다크) + PC 1280px(라이트)로 PAGES를 전체 길이로 찍어 <접두어>_<화면>_<모드>.png 로 저장.
// 캡처용 임시 사용자를 하나 만들고 설문을 채운다 → 끝나면 그 사용자 ID를 출력 (확인 뒤 DB에서 지울 것).
// 검색 화면을 찍으며 search_logs에 "RPG"가 남으니 그것도 지울 것 (인기 검색어·급상승 계산에 섞이지 않게)
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.env.CAPTURE_BASE || "http://localhost:8000";
const CHROME = [
  process.env.CHROME,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "/usr/bin/google-chrome",
].find((p) => p && existsSync(p));
const PORT = 9333;
const PAGES = [
  ["home", "/static/pages/home.html"],
  ["ranking", "/static/pages/ranking.html"],
  ["new", "/static/pages/new.html"],
  ["game", "/static/pages/game.html?id=stardew"],
  ["search", "/static/pages/search.html?q=RPG"],
  ["mypage", "/static/pages/mypage.html"],
  ["settings", "/static/pages/settings.html"],
];
const SURVEY = { q1: ["ios"], q2: ["any"], q3: ["genre:RPG", "mood:판타지"], q4: ["genre:캐주얼"], q5: ["dq8"] };
const MODES = [["m-light", 375, 812, true, "light"], ["m-dark", 375, 812, true, "dark"], ["pc-light", 1280, 900, false, "light"]];

const [outDir = "docs/ui_c12", prefix = "capture"] = process.argv.slice(2);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  if (!CHROME) throw new Error("Chrome/Edge를 찾지 못함 (CHROME 환경 변수로 경로 지정)");
  mkdirSync(outDir, { recursive: true });
  const profile = mkdtempSync(join(tmpdir(), "gp-capture-"));
  const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
    "--no-first-run", "--hide-scrollbars", "about:blank"], { stdio: "ignore" });
  try {
    let targets;
    for (let i = 0; i < 50 && !targets; i++) {
      await sleep(200);
      targets = await fetch(`http://127.0.0.1:${PORT}/json/list`).then((r) => r.json()).catch(() => null);
    }
    const page = targets.find((t) => t.type === "page");
    const ws = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
    let id = 0;
    const pending = new Map();
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id && pending.has(msg.id)) {
        const { resolve, reject } = pending.get(msg.id);
        pending.delete(msg.id);
        if (msg.error) reject(new Error(msg.error.message)); else resolve(msg.result);
      }
    };
    const send = (method, params = {}) => new Promise((resolve, reject) => {
      const n = ++id;
      pending.set(n, { resolve, reject });
      ws.send(JSON.stringify({ id: n, method, params }));
    });
    const evaluate = (expression) => send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    const go = async (path) => {
      await send("Page.navigate", { url: BASE + path });
      await sleep(2500);  // 화면 그리기 + API 응답 대기
    };

    await send("Page.enable");
    await go("/static/pages/settings.html");   // 쿠키(임시 사용자) 발급
    const answer = await evaluate(`fetch("/api/survey/answers", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({answers: ${JSON.stringify(SURVEY)}})}).then((r) => r.status)`);
    if (answer.result.value !== 200) throw new Error("설문 저장 실패: " + JSON.stringify(answer.result));

    for (const [mode, width, height, mobile, scheme] of MODES) {
      await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile });
      await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: scheme }] });
      for (const [name, path] of PAGES) {
        await go(path);
        const { cssContentSize } = await send("Page.getLayoutMetrics");
        const shot = await send("Page.captureScreenshot", {
          format: "png", captureBeyondViewport: true,
          clip: { x: 0, y: 0, width, height: Math.min(Math.ceil(cssContentSize.height), 6000), scale: 1 },
        });
        const file = join(outDir, `${prefix}_${name}_${mode}.png`);
        writeFileSync(file, Buffer.from(shot.data, "base64"));
        console.log(file);
      }
    }
    const { cookies } = await send("Network.getAllCookies");
    console.log("캡처용 사용자 쿠키:", cookies.filter((c) => c.domain.includes("localhost")).map((c) => `${c.name}=${c.value}`).join("; "));
    ws.close();
  } finally {
    chrome.kill();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});

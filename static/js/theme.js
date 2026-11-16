/*
 * 화면 모드를 그리기 전에 적용한다 (깜빡임 방지). <head>에서 defer 없이 가장 먼저 불러온다.
 * 저장값: localStorage "gp-theme" = "light" | "dark" | 없음(기기 설정 따라감)
 * 전환 버튼·설정 화면은 common.js의 setTheme / toggleTheme을 쓴다.
 */
(function () {
  var saved = null;
  try {
    saved = localStorage.getItem("gp-theme");
  } catch (e) {
    // 저장소를 못 쓰는 환경(사생활 보호 모드 등)이면 기기 설정을 따른다
  }
  if (saved === "light" || saved === "dark") {
    document.documentElement.setAttribute("data-theme", saved);
  }
})();

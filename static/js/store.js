// 스토어 링크 고르기 (게임 상세). 화면 코드와 떼어 두어 tests/test_store_link.py가 node로 바로 확인한다.
// - iOS(iPhone·iPod·iPad, 데스크톱 모드 iPad 포함)는 App Store만, Android는 Google Play만.
//   다른 쪽 스토어로 보내면 설치가 안 되니 링크가 없으면 null → "준비 중" 안내
// - 그 밖(PC 등)은 Google Play 먼저, 없으면 App Store

export function platformOf(nav) {
  const ua = nav.userAgent || "";
  if (/iPhone|iPad|iPod/i.test(ua)) return "ios";
  // iPadOS 13+ 기본 설정은 데스크톱 Safari처럼 Mac으로 보임 → 터치 지점 수로 구분
  if (nav.platform === "MacIntel" && nav.maxTouchPoints > 1) return "ios";
  if (/Android/i.test(ua)) return "android";
  return "pc";
}

export function storeUrl(game, nav) {
  const platform = platformOf(nav);
  if (platform === "ios") return game.ios_url || null;
  if (platform === "android") return game.android_url || null;
  return game.android_url || game.ios_url || null;
}

export function missingStoreMessage(nav) {
  const platform = platformOf(nav);
  if (platform === "ios") return "App Store 링크 준비 중이에요";
  if (platform === "android") return "Google Play 링크 준비 중이에요";
  return "스토어 링크 준비 중이에요";
}

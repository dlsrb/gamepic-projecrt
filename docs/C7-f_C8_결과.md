# C7-f · C8 결과 (11/9) — PROJECT.md 4.11에 넣은 것과 같은 내용의 사본

> PROJECT.md가 예전 내용으로 여러 번 덮어써져서(11/9 15:04·15:07·15:32 저장본) 결과를 이 파일에도 남긴다.
> PROJECT.md에서 이 내용이 사라졌으면 이 파일을 기준으로 다시 넣으면 된다.

---

## 결과 (C7-f, 11/9)

- **% 보정**: `reco_config.MATCH_MIN_TAGS = 4`, `coverage()`에 `min(1, 태그 수 ÷ 4)`를 곱함. 추천 순서(score)는 그대로
  - ⚠ 지시서 테스트의 "태그 2개 게임 % ≤ 50"은 식대로면 **일치율** 기준만 맞음. 화면 % = 0.7 × 일치율 + 0.3 × 1등 대비라 태그 2개 게임은 최대 0.7 × 50 + 30 = **65%**. 테스트는 "일치율 = 50%, 화면 % ≤ 65"로 씀. 실제 6명 기준 태그 2개 게임 최고는 60%
  - Geometry Dash·파피 플레이타임(태그 2개), 퍼즐·캐주얼 취향(시뮬 C): **90% → 60%**
  - **C7-e 후 → C7-f 후** (같은 게임 86개, 엔진 후보 상위 30개. 최소 | 최대 | 평균 | 70% 이상)

    | 사용자 | 후보 | C7-e 후 | C7-f 후 |
    |---|---|---|---|
    | 실제 1 (random) | 27 | 28 \| 77 \| 45.7 \| 2 | 28 \| 65 \| 44.5 \| **0** |
    | 실제 2 (survey) | 30 | 24 \| 95 \| 56.5 \| 6 | 24 \| 85 \| 50.4 \| **2** |
    | 시뮬 A | 30 | 30 \| 82 \| 55.1 \| 5 | 30 \| 79 \| 49.6 \| **1** |
    | 시뮬 B | 30 | 30 \| 99 \| 57.7 \| 8 | 30 \| 99 \| 56.0 \| **6** |
    | 시뮬 C | 22 | 34 \| 90 \| 62.7 \| 11 | 34 \| 77 \| 55.8 \| **5** |
    | 시뮬 E | 30 | 37 \| 94 \| 61.3 \| 10 | 37 \| 94 \| 60.2 \| **9** |

  - ⚠ 목표와 다름 → 숫자 안 바꾸고 보고: 시뮬 A 1개, 실제 사용자 2·0개 → **C8-a에서 홈 60% + 태그 4개 이상 규칙으로 해결**
- **최근 출시작 수집** (`scripts/sync_store.py`의 `add_new_releases()`, 서버 하루 1번 실행에 포함)
  - App Store 신작 피드 `newapplications`·`newfreeapplications`·`newpaidapplications` 셋 다 됨 (각 109개, 거의 같은 목록). `lookup`의 `primaryGenreId`/`genreIds`에 6014가 있는 것만
  - Google Play: ① App Store 신작의 짝(`bundleId` → 이름 검색) ② `data/new_release_queries.json` 검색어 8개 × 상위 20개
  - "최근" = `NEW_RELEASE_DAYS = 90`. 출시 예정 제외: 출시일이 없거나 오늘 이후, Android 설치 수 0 (Play 라이브러리가 "사전 등록" 표시를 안 줘서 이 두 가지로 판단). Play 쪽만 사전 등록이면 짝을 안 붙이고 iOS만으로 넣음
  - 상한 `NEW_RELEASE_MAX_PER_RUN = 100` (차트 50과 따로, `--new-limit`), `games.source_feed`(manual/chart/new, 기존 자동 수집 50개는 chart로 채움)
  - `--prune-new --min-installs N --min-ratings N [--dry-run]`: 신작 경로 게임 중 기준 미달을 `rejected`로 숨김(지우지 않음). 설치 수 기준은 설치 수를 아는(Android) 게임에만, 별점 수는 없으면 0. **실행 안 함**
- **실제 실행** — 백업: `C:\Users\user\AppData\Local\Temp\claude\2a66d664f6541fa9\70a5f9f8-6699-4b4d-b5d0-167f46547007\scratchpad\gamepick_before_c7f.db`
  - 차트: 6개 모두 성공, 추가 46 + 검수 대기 2, 기존 87개 숫자 갱신, 실패 0
  - **신작: 추가 40** (iOS만 27 / Android만 3 / 두 스토어 10) + 검수 대기 17. 후보: App Store 피드 57, Play 검색 103. 걸러진 것: 게임 아님 176, 90일 넘음 83, 출시 예정 2, 상한으로 미룸 0
  - Play 검색으로 실제로 들어온 건 Android만 3개 (검색 결과 대부분이 90일 넘은 게임)
  - 신작 40개 분포: 설치 수 — 모름(iOS만) 27 · 1~999 9 · 10만+ 4 / 별점 수 — **0인 게임 36** · 1000+ 4 / 자동 태그 수 — 1개 6 · 2개 10 · 3개 12 · 4개 6 · 5~6개 6
  - ⚠ 한국 신작 피드는 해외 소형 앱이 대부분 (40개 중 이름에 한글 있는 것 4개) → C8-a에서 신작 탭 정렬로 처리
  - ⚠ 신작 검수 대기 17개는 대부분 오탐 (iOS 신작의 Play 짝 후보만 애매) → C8-a에서 iOS만으로 넣기로
  - 이미 들어온 게임 중 출시 예정으로 보이는 것: 라이징파이터 (출시일 10/30, Android 설치 수 0) — 지우지 않음
  - 고친 버그: 검수 대기를 두 번 세던 것 (로그 "검수 대기 21" → 실제 19, 차트 게임이 2개 덜 들어옴). 이번 실행 기록(`sync_runs` 2번)도 19로 바로잡음
- 테스트: **138개 모두 통과** (132 + 새 6). 기존 테스트 고친 것 없음
- 지시서에 없던 결정: `--prune-new`에 `--dry-run` / 설치 수 기준은 설치 수를 아는 게임에만 / 신작 피드 3개 중복은 한 번만 / Play 쪽 사전 등록이면 짝 안 붙임

---

## 결과 (C8, 11/9) — 지시: `docs/C8_지시.md`

- 시작 전 백업: `C:\Users\user\AppData\Local\Temp\claude\2a66d664f6541fa9\70a5f9f8-6699-4b4d-b5d0-167f46547007\scratchpad\gamepick_before_c8.db`

### C8-a. 취향 % 완화 + 신작 정리
- `MATCH_MIN_TAGS = 4` 그대로. `reco_config.HOME_MIN_TAGS = 4` 추가 → `services.match_feed()`가 `min_match > 0`일 때 기준 통과 칸에 **취향 % ≥ min_match 그리고 태그 4개 이상**만 넣음 (random 그룹도 같음). 대체 동작·탐색 칸은 상관없음. 홈 `MIN_MATCH` 70 → **60** (안내 문구도 60%)
- **C7-f 후 → C8-a 후** 기준 통과 개수 (지금 DB 게임 187개 = C7-f 수집 후, 후보 전체 기준. C7-f 기준 = 70% 이상 / C8-a 기준 = 60% 이상 + 태그 4개 이상)

  | 사용자 | 후보 | C7-f 기준 | C8-a 기준 | 홈 슬라이드 |
  |---|---|---|---|---|
  | 실제 1 (random) | 42 | 0 | **3** | 3장 (random 그룹은 탐색 칸 없음), 대체 동작 없음 |
  | 실제 2 (survey) | 83 | 5 | **6** | 9장 |
  | 시뮬 A | 100 | 3 | **9** | 9장 |
  | 시뮬 B | 98 | 8 | **20** | 9장 |
  | 시뮬 C | 75 | 10 | **19** | 9장 |
  | 시뮬 E | 99 | 11 | **24** | 9장 |

  - 실제 사용자 3개 이상 ✅, 잘 맞는 시뮬은 5~10개 목표를 넘어 9~24개 (게임이 187개로 늘어서). 슬라이드는 최대 9장이라 화면엔 영향 없음
  - **시뮬 C 슬라이드에 Geometry Dash·파피 플레이타임 안 나옴** ✅ (Clicker DIY, 햄도쿠, Foodie Pop!, Cutie Sort, Sand Blocks, 블록게임, 머지 쿡토피아 + 탐색 어몽어스·모뉴먼트 밸리)
- **검수 대기 규칙 변경**: iOS 게임의 Android 짝을 이름 검색으로만 찾았는데 개발사가 다르면 → 검수 대기 대신 **iOS만으로 넣음** (`pair_android()`). 검수 대기는 `find()`에서 DB 게임과 이름이 비슷할 때만. 차트·신작 경로 모두 같은 함수라 둘 다 적용
- **기존 검수 대기 다시 판정**: 새 명령 `--recheck-pending [--dry-run]` (DB의 다른 게임(제외된 것 빼고)과 이름 유사도 0.6 이상이 없으면 ok)
  - 미리 보기(`--dry-run`) 목록 — iOS 이름 ⇢ 잘못 걸렸던 Android 앱 (로그 `logs/sync_20261109.log`에서):
    - → ok (15): GRAV - Gravity Ball ⇢ Gravity Ball / Hoverkiss: Rocket Landing ⇢ Rockets Landing / Pixel Harmony ⇢ Harmony®(Logitech) / Bauernschnapsen: Online ⇢ Bauernschnopsn - 4er Schnapsen / Chroma Duel ⇢ Chroma Duel / Dispatch City ⇢ City Dispatch / DuckHoops ⇢ Dunk Hoop / Lighthouse Keeper: Idle Light ⇢ Lighthouse Keeper / Pleh ⇢ pleh.tech / SHAYP MATCH ⇢ shayp / Hopvolt: Neon Bounce ⇢ Neon Bounce / Boomies ⇢ BOOMSY / Arrowhaven: 3D 화살표 퍼즐 ⇢ Arrow Puzzle 3D / Backsolve ⇢ DailySolve / Bubblix ⇢ BUBBLIX
    - 그대로 검수 대기 (4, DB에 이름 비슷한 게임 있음): Despotism 3k ↔ DB "Despotism 3k (전제정치 3k)"(konfa games) / Cutie Sort - 귀여운 색깔 분류 퍼즐 ↔ DB "Cutie Sort: 고양이 분류 퍼즐"(Easetouch) / BlokVex ↔ 로블록스 / Bloom Block ↔ Close Combo
  - 적용: 15개 ok. 남은 4개는 본인 판단 필요 (Despotism 3k·Cutie Sort는 같은 게임의 다른 스토어일 수 있음, BlokVex·Bloom Block은 다른 게임으로 보임 → `--approve`)
  - 블록 블라스트 ↔ 로블록스는 11/9 채팅에서 이미 `--approve` 함
- **신작 탭 정렬** (`/api/feed?section=new`): 이름에 한글이 있거나 별점 수 1개 이상인 게임 먼저 → 나머지, 각 묶음 안에서 최신순. 숨기지 않음. 지금 신작 페이지 맨 앞: Ink Chaos: 2인 잉크 대결, Arrowhaven: 3D 화살표 퍼즐, 전생했더니 검 한 자루 …
- 라이징파이터 → 7장 할 일로

### C8-b. 하단 메뉴 + 랭킹·신작 페이지
- `common.js`에 `bottomNav(active)`: 홈 🏠 · 랭킹 🏆 · 신작 ✨ · 설정 ⚙️, 지금 페이지는 강조 + `aria-current="page"`. `body` 끝에 붙이고 `body.has-bottom-nav`로 본문 아래 여백(메뉴 높이 + `env(safe-area-inset-bottom)` + 24px), 알림(toast)도 메뉴 위로. 폭은 `--page-max`(480px), 색은 기존 변수. 높이 `--nav-height: 56px`
  - 보이는 페이지: 홈·랭킹·신작·설정(강조) / 상세·검색·마이페이지(강조 없음). 설문 페이지에는 없음
  - 메뉴가 보이는 페이지의 viewport에 `viewport-fit=cover` 추가 (아이폰 홈 바 여백이 실제로 적용되게)
- **랭킹** `static/pages/ranking.html` + `static/js/ranking.js`: 탭 "게임픽 추천 순위"(`/api/ranking`, 홈과 같은 목록 모양 → `common.js`의 `rankList()`로 공통화) / "스토어 인기"(`/api/feed?section=popular&limit=30`, 순위 번호 + 카드). `?tab=store`로 바로 열기
- **신작** `static/pages/new.html` + `static/js/new.js`: C8-a 정렬, 카드 아래 "11/7 출시", 20개씩 "더 보기"
- `/api/feed`에 `offset`(0~10000, `new`·`popular`에서 사용) + 응답 `has_more`
- `pages.ranking()`, `pages.newGames()` 추가
- **홈 정리**: 검색창 → 맞춤 추천 슬라이드 → 추천 순위 상위 5개 + "전체 보기 ›"(→ 랭킹) → 최근 본 게임. "인기 · 신작" 탭 섹션 삭제 (`.home-tabs` CSS → 랭킹용 `.page-tabs`)
- **오른쪽 위 설정 버튼**: 홈 `topBar` `right: ["profile"]`로. 다른 페이지엔 원래 없었음. 개발용 미리 보기(`preview.js`)도 같이. `topBar`의 `settings` 항목 자체는 남겨 둠 (안 쓰지만 지워도 됨)
- 상세의 오른쪽 위 "홈" 버튼은 그대로 (하단 메뉴와 겹침 — 지우자면 상세·검색·마이페이지·설정·랭킹·신작의 "홈" 버튼을 같이 정리하는 걸 제안)

### C8-c. 상세 댓글
- DB `comments`(id, game_id → games ON DELETE CASCADE, user_id → users ON DELETE CASCADE, body, created_at, updated_at) + 인덱스 (game_id, created_at). `init_db.py`가 이미 있는 DB에도 만듦 (실제 DB에 추가함)
- API (`app/routers/comments.py`)
  - `GET /api/games/{id}/comments?before_id=&limit=20` → `{total, comments: [{id, nickname, body, created_at, edited, mine}], has_more}` (없는 게임·검수 대기 게임 404, limit 1~50)
  - `POST /api/games/{id}/comments` `{body}` → 201 / `PATCH /api/comments/{id}` / `DELETE /api/comments/{id}` → 내 것만 (남의 것 403, 없으면 404). 삭제는 실제 삭제
- 검사: `\r\n` → `\n`, 제어 문자 + 보이지 않는 글자 방향 문자(U+200B~200F, U+202A~202E, U+2066~2069, U+FEFF) 제거, 앞뒤 공백 제거 → 1~300자, 10줄 이하. 다른 필드가 오면 422
- 요청 제한 (`app/security.py`의 `comment_rate_limit`): 쓰기·고치기 합쳐 1분 5번(사용자·IP 각각), 하루 50번(사용자만) → 429. `RateLimiter.hit()`에 기간(window) 인자 추가
- 화면 (`game.js`): "별점" 아래 "댓글 N" → 입력창(글자 수 12/300, 300자·10줄 넘으면 등록 버튼 꺼짐) → 최신순 목록(닉네임 없으면 "익명 사용자", "방금"/"N분 전"/"N시간 전"/"11/9", "(수정됨)") → 20개씩 "댓글 더 보기". 내 댓글에만 고치기(그 자리에서)·지우기(확인 창). 본문은 `textContent` + `white-space: pre-wrap`
- 내 데이터 내려받기에 `comments` 포함, 내 데이터 삭제 → 연쇄 삭제, 추천 기록 초기화는 댓글 안 지움. 개인정보 처리방침 초안에 "내가 쓴 댓글" 저장 + "닉네임과 함께 공개" 문구, 삭제 확인 문구에 "댓글" 추가

### C8-d. 확인 (브라우저, 모바일 375px, 라이트/다크)
- 설문 페이지에 메뉴 없음 / 홈: 메뉴 4개(홈 강조), 오른쪽 위는 🌙·프로필만, 섹션 = 맞춤 추천·추천 순위(전체 보기 → 랭킹)·최근 본 게임, 메뉴가 본문(마지막 내용 아래 732px < 메뉴 위 755px)·슬라이드 점을 가리지 않음, 가로 넘침 없음
- 랭킹: 메뉴 "랭킹" 누르면 이동·강조, 탭 2개 전환(주소 `?tab=store`), 스토어 인기 30개 (두 스토어 중 더 높은 순위라 1·1·3·3처럼 같은 번호가 나옴)
- 신작: 강조, 한글·별점 있는 게임 먼저, 20개 → 더 보기 → 40개
- 설정: 강조
- 상세 댓글: 빈 글이면 등록 꺼짐, 서버도 빈 글·301자 422("댓글은 300자까지 쓸 수 있어요. (지금 301자)"), `<b>` 글자가 그대로 글자로 보임, 등록 → "댓글 1"·고치기·지우기, 고치기 → "(수정됨)", 지우기 → "댓글 0"·실제 삭제. 다크 모드 정상. 콘솔 오류는 일부러 보낸 422 두 건뿐. "댓글 더 보기"는 요청 제한(분당 5개) 때문에 브라우저에선 안 해 보고 테스트로 확인. 시험 사용자·댓글은 지움
- 테스트: **150개 모두 통과** (138 + 새 12: 홈 60%·태그 4개 이상(feedback·random), 신작 정렬, offset·has_more, 댓글 쓰기·목록·고치기·지우기, 길이·줄 수·제어 문자·다른 필드, 남의 댓글 403·없는 댓글·게임 404·limit 51, before_id 더 보기, 분당·하루 제한 429, 내려받기·초기화·삭제, 이름 검색 짝이 다른 개발사면 iOS만, 검수 대기 다시 판정). 기존 테스트 고친 것 없음

### 지시서에 없던 결정 (C8)
1. `HOME_MIN_TAGS`는 `/api/feed`에 `min_match`가 있을 때만 적용 (지금은 홈만 씀)
2. 검수 대기 규칙 변경을 차트 경로에도 똑같이 적용 (같은 함수)
3. 검수 대기 다시 판정용 `--recheck-pending` 명령 추가
4. 랭킹 페이지 `?tab=store` 주소, 스토어 인기는 30개만(더 보기 없음 — 지시서에 신작만 더 보기)
5. 댓글 하루 50번 제한은 사용자 기준만 (같은 와이파이 여러 명이 막히지 않게), 분당 5번은 사용자·IP 둘 다
6. 댓글 시간에 "N시간 전" 추가 (지시서 예: 방금·3분 전·11/9)
7. 검수 대기·제외된 게임에는 댓글 못 씀 (404)
8. 제어 문자 외에 보이지 않는 글자 방향 문자도 제거
9. 지시서의 "출처 검사"는 지금 `app/security.py`에 없음 — 쿠키 `SameSite=lax` + JSON 본문만 받는 방식이 기존 보호. 댓글도 같음. 출처(Origin) 검사를 따로 넣을지는 결정 필요
10. 댓글 요청 제한은 내용이 잘못된(422) 요청도 횟수에 셈

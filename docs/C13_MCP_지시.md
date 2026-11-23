# C13 지시 — 게임픽 MCP 서버 (작게, 가점용) (11/23 채팅)

> `PROJECT.md`와 같이 읽고 시작. 결과는 PROJECT.md 4.11 "결과 (C13)", 지시 외 결정은 따로.
> 목표: 과목 가점 항목 "AI 에이전트(MCP) 구현". **배포 사이트·Replit은 건드리지 않음.** 본인 PC의 Claude 데스크톱에 연결해 시연.
> 원칙 (4.1): AI는 **게임픽 DB 도구 결과만 근거로** 답한다. 추천 결정은 기존 추천 엔진이 하고, AI는 묻고 설명하는 역할.
> **먼저 조사 후 진행**: 지금 쓸 수 있는 MCP Python SDK(PyPI `mcp`)의 버전·사용법(FastMCP, stdio)과 Claude 데스크톱 연결 방법(설정 파일 위치·형식)을 공식 문서로 확인하고, 결과를 PROJECT.md에 적은 뒤 구현. 지시와 다르면 문서 쪽을 따르고 결정에 적기.

## 1. 구조
- 새 폴더 `mcp_server/` (파일 이름 ASCII)
  - `gamepick_mcp.py`: MCP 서버 (stdio)
  - `README.md`: 설치·연결·시연 방법 (한국어)
- 의존성은 **`requirements-mcp.txt`로 따로** (`mcp` 등). 기존 `requirements.txt`에 넣지 않음 → Replit 배포·보안 검사에 영향 없게
- `pack_for_replit.py`의 zip에서 `mcp_server/`·`requirements-mcp.txt` 제외
- DB는 `data/gamepick.db`를 **읽기 전용**(`mode=ro`)으로 연다. 쓰기 도구 없음
- 기존 코드 재사용: 검색 해석(`app/routers/search.py`의 로직), 추천(`app/reco/`), 랭킹(`app/rankings.py`), 비선호 연관 제외(`dislike_related.json`). 필요하면 라우터에서 함수만 떼어 내고 기존 API 동작은 그대로

## 2. 도구 (4개)
모든 도구는 결과에 게임 `id`·이름·장르·태그·태그 출처(`tag_source`)·스토어 링크를 담아, AI가 근거를 들어 설명할 수 있게. 검수 상태(`review_status`)가 ok인 게임만.
1. `search_games(query, limit=10)`: 게임픽 검색과 같은 해석(태그·동의어·이름 부분 일치, "신작·인디·인기·급상승" 배지 검색 포함)
2. `get_game(game_id)`: 상세 — 설명, 태그, 배지, 별점·설치 수, 출시일, 스토어 링크, 비슷한 게임 5개
3. `recommend_games(likes, dislikes=[], favorite_game_ids=[], limit=10)`: 태그 이름(종류 없이, 예 "퍼즐", "힐링")으로 임시 취향을 만들어 **기존 추천 엔진**으로 추천. 비선호 연관 제외 적용, 검수 게임 우선(C12-i 규칙 그대로). 결과에 취향 %와 추천 이유(기존 `reasons`)
4. `get_rankings(kind="popular", tab="전체", limit=10)`: 인기 랭킹(전체/Google Play/App Store/장르 탭) · 추천 순위 · 이달의 게임 · 급상승
- 잘못된 태그 이름·게임 id면 오류 대신 "없는 태그: …, 쓸 수 있는 태그는 …" 같은 안내를 돌려줌
- 사용자 개인 데이터(사용자·댓글 작성자·반응 기록)는 어떤 도구도 내보내지 않음

## 3. 시연 준비
- `mcp_server/README.md`에: 설치(`pip install -r requirements-mcp.txt`), Claude 데스크톱 설정 예시(이 PC의 Python·경로 기준), 연결 확인 방법
- 시연 질문 예시 5개와 기대 동작 (예: "힐링되는 퍼즐인데 광고 적은 거", "RPG 좋아하는데 가챠는 싫어", "이번 달 인기 게임 중 혼자 하는 거", "스타듀 밸리랑 비슷한 거", "인디 게임 추천")
- MCP Inspector 등 공식 점검 도구로 도구 목록·호출이 되는지 확인한 결과를 적기 (가능하면)

## 4. 확인
- 테스트: 도구 4개를 직접 함수로 호출 (정상, 없는 태그, 없는 id, 비선호 제외, 개인 데이터 없음, DB 읽기 전용이라 쓰기 시도 실패)
- 기존 테스트 모두 통과
- zip 다시 만들어 `mcp_server/`가 빠졌는지 확인
- `git status` 새 파일 목록을 결과에 적기. 커밋은 본인이 함

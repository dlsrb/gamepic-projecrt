# 게임픽 MCP 서버

Claude 데스크톱 같은 MCP 클라이언트에 붙이면, AI가 **게임픽 DB를 도구 4개로 조회해서 그 결과만 근거로** 답한다.
추천 순서는 게임픽 추천 엔진(`app/reco`)이 정하고, AI는 묻고 설명하는 역할이다 (PROJECT.md 4.1 "LLM의 역할은 보조").

- 전송: stdio (내 PC에서 Claude 데스크톱이 이 파일을 직접 실행)
- DB: `data/gamepick.db`를 **읽기 전용**으로 연다 (`mode=ro` + `query_only`). 쓰기 도구 없음, 검색 기록도 안 남김
- 화면에 보이는 게임(`review_status='ok'`)만. 사용자·닉네임·댓글·반응 기록은 어떤 도구도 내보내지 않음 (추천 순위의 "추천 수"처럼 모아 센 숫자만)
- 배포 사이트(Replit)와 무관 — `pack_for_replit.py`가 이 폴더와 `requirements-mcp.txt`를 zip에서 뺀다

## 도구

| 도구 | 하는 일 |
|---|---|
| `search_games(query, limit=10)` | 화면 검색과 같은 해석: 태그(퍼즐·힐링·2D …) / 동의어(무료·혼자·친구·도트 …) / 자동 배지(신작·인디·인기·급상승) / 이름·태그 부분 일치, 모두 만족 |
| `get_game(game_id)` | 상세: 설명, 종류별 태그, 태그 출처, 배지, 별점·설치 수, 출시일, 스토어 순위·링크, 비슷한 게임 5개 |
| `recommend_games(likes, dislikes=[], favorite_game_ids=[], limit=10)` | 태그 이름으로 임시 취향을 만들어 **게임픽 추천 엔진**으로 추천. 비선호 + 연관 태그 제외(캐주얼 → 귀여움 등), 태그 검수된 게임 먼저. 결과에 취향 %(`match`)와 맞는 태그(`reasons`) |
| `get_rankings(kind="popular", tab="전체", limit=10)` | `popular` 인기 랭킹(전체·Google Play·App Store·RPG·시뮬레이션·액션) / `likes` 추천 순위 / `monthly` 이달의 게임 / `surge` 인기 급상승 |

모든 게임 결과에 `id`·이름·장르·태그·**태그 출처**(`tag_source`: human 사람 · ai AI 초안 승인 · store 스토어 분류 · auto 자동 규칙)·검수 여부·배지·스토어 링크가 들어 있어 AI가 근거를 들어 설명할 수 있다.
잘못된 태그 이름·게임 id·탭이면 오류 대신 안내(쓸 수 있는 태그 목록, 비슷한 id 후보 등)를 돌려준다.

## 설치

게임픽 앱이 돌아가는 같은 Python(3.10 이상)에서:

```
pip install -r requirements.txt
pip install -r requirements-mcp.txt
```

`requirements-mcp.txt`는 `mcp==2.3.0` (MCP Python SDK 2.x, 서버는 `from mcp.server import MCPServer`).

## 연결 확인 (Claude 데스크톱 없이)

```
python mcp_server/smoke_test.py
```

SDK 클라이언트로 서버를 띄워 도구 목록과 도구 4개 호출을 확인하고 마지막에 `OK`.

MCP 공식 점검 도구(Inspector, Node.js 필요)로도:

```
npx -y @modelcontextprotocol/inspector --cli python mcp_server/gamepick_mcp.py --method tools/list
npx -y @modelcontextprotocol/inspector --cli python mcp_server/gamepick_mcp.py --method tools/call --tool-name search_games --tool-arg "query=인디 신작" --tool-arg limit=3
```

화면으로 보려면 `--cli` 없이 실행하면 브라우저에 Inspector가 열린다.

## Claude 데스크톱에 연결

설정 파일 `claude_desktop_config.json` (없으면 만든다)
- Windows: `%AppData%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

이 PC 기준 예시 (Python·폴더 경로는 절대 경로, 역슬래시는 `\\`):

```json
{
  "mcpServers": {
    "gamepick": {
      "command": "C:\\Users\\user\\anaconda3\\envs\\gamepic-project\\python.exe",
      "args": ["C:\\Users\\user\\PycharmProjects\\gamepic project\\mcp_server\\gamepick_mcp.py"],
      "env": { "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

저장한 뒤 **Claude 데스크톱을 완전히 종료했다가 다시 켠다**. 대화창의 도구(커넥터) 목록에 `gamepick`과 도구 4개가 보이면 연결된 것.
다른 DB를 보려면 `env`에 `"DB_PATH": "절대 경로"`를 더한다 (기본은 `data/gamepick.db`).

※ stdio 서버라 서버 코드에서 `print()`를 쓰면 안 된다 (MCP 메시지가 깨짐). 로그는 stderr로만 나간다.

## 시연 질문 예시 (기대 동작)

결과는 지금 로컬 DB 기준 (게임이 늘면 바뀜).

1. **"힐링되는 퍼즐인데 광고 적은 거 추천해줘"**
   → `recommend_games(likes=["퍼즐", "힐링"], dislikes=["광고"])`. 퍼즐 장르만, 광고 태그 게임 제외, 검수된 게임 먼저:
   캔디크러시 사가 · 로얄 매치 · 모뉴먼트 밸리 · 로얄 킹덤 … AI는 `reasons`(맞는 태그)와 `tag_source`로 설명
2. **"RPG 좋아하는데 가챠는 싫어"**
   → `recommend_games(likes=["RPG"], dislikes=["가챠"])`: 애니모 · 드래곤 퀘스트 VIII · 미르의 전설2 · 총잡이 고양이 · 던전앤파이터 모바일 …
3. **"이번 달 인기 게임 중 혼자 하는 거"**
   → `get_rankings(kind="popular")` 또는 `get_rankings(kind="monthly")`로 받고 태그에 `싱글`이 있는 게임만 골라 설명
   (또는 `search_games("인기 혼자")` — "혼자" = 싱글 동의어, "인기" = 차트 30위 배지)
4. **"스타듀 밸리랑 비슷한 거"**
   → `search_games("스타듀")`로 id(`stardew`) → `get_game("stardew")`의 `similar_games`, 또는
   `recommend_games(likes=[], favorite_game_ids=["stardew"])`: 나 혼자만 상한가 · 마이 시크 몰 · 마인크래프트 …
5. **"인디 게임 추천"**
   → `search_games("인디")` (배지: 설치 수 10만 이하 등, 지금 87개): 다키스트 던전 · GRIS · 디노몬 …

시연 포인트: AI가 도구 결과에 없는 게임을 지어내지 않는지, 추천 이유를 태그로 대는지, `auto`·`store` 태그 게임은 "태그가 확인 전"이라고 말하는지.

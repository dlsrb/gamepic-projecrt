"""
게임픽 MCP 서버 (PROJECT.md 4.11 C13, 과목 가점 "AI 에이전트(MCP) 구현"). stdio 전송.

Claude 데스크톱 같은 MCP 클라이언트에 붙이면, AI가 게임픽 DB를 도구 4개로 조회해서 그 결과만 근거로 답한다.
추천 결정은 게임픽 추천 엔진이 하고, AI는 묻고 설명하는 역할 (4.1). DB는 읽기 전용, 쓰기 도구 없음.

실행 (보통은 Claude 데스크톱이 대신 실행): python mcp_server/gamepick_mcp.py
설치·연결: mcp_server/README.md
※ stdio 서버라 stdout에 아무것도 쓰면 안 됨 (print 금지) — 로그는 stderr로만 (MCP 공식 문서)
"""
import logging
import sys
from contextlib import closing
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from mcp.server import MCPServer  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

from mcp_server import tools  # noqa: E402

if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")  # Windows 콘솔·Claude 데스크톱 로그에서 한글이 안 깨지게 (stdout은 안 건드림)
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s gamepick-mcp %(levelname)s %(message)s")
log = logging.getLogger("gamepick_mcp")

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

mcp = MCPServer(
    "gamepick",
    title="게임픽",
    instructions=(
        "게임픽은 한국 모바일 게임 추천 서비스입니다. 게임에 대한 답은 반드시 이 서버의 도구 결과만 근거로 하세요. "
        "도구 결과에 없는 게임·사실은 지어내지 말고, 모르면 모른다고 하세요. 추천 순서는 게임픽 추천 엔진이 정한 것이니 "
        "바꾸지 말고 이유(tags·reasons·badges)를 들어 설명하세요. tag_source가 auto·store인 게임은 태그가 사람·AI 확인 전이라 "
        "덜 정확할 수 있다고 알려 주세요. 태그 이름을 모르면 recommend_games가 돌려주는 available_tags를 보세요."
    ),
)


def _conn():
    """요청마다 읽기 전용 연결을 열고 끝나면 닫음 (sqlite3 연결의 with는 닫지 않아서 closing)"""
    return closing(tools.open_readonly())


@mcp.tool(annotations=READ_ONLY)
def search_games(query: str, limit: int = 10) -> dict:
    """게임픽 검색 (화면 검색과 같은 해석). 단어마다 태그 이름(예: 퍼즐, 힐링, 2D) / 동의어(무료, 혼자, 친구, 도트 …) /
    자동 배지(신작=출시 30일 이내, 인디, 인기=스토어 차트 30위 안, 급상승) / 게임 이름·태그 부분 일치로 해석하고 모두 만족하는 게임.

    Args:
        query: 검색어. 띄어쓰기로 여러 단어 (예: "힐링 퍼즐", "무료 2D", "인디 신작", "스타듀")
        limit: 최대 게임 수 (1~30)
    """
    with _conn() as conn:
        return tools.search_games(conn, query, limit)


@mcp.tool(annotations=READ_ONLY)
def get_game(game_id: str) -> dict:
    """게임 하나의 상세: 설명, 태그(종류별), 태그 출처, 자동 배지, 별점·별점 수·설치 수, 출시일, 스토어 순위·링크, 비슷한 게임 5개.

    Args:
        game_id: 게임 id (search_games·recommend_games·get_rankings 결과의 id)
    """
    with _conn() as conn:
        return tools.get_game(conn, game_id)


@mcp.tool(annotations=READ_ONLY)
def recommend_games(likes: list[str], dislikes: list[str] | None = None,
                    favorite_game_ids: list[str] | None = None, limit: int = 10) -> dict:
    """좋아하는·싫어하는 태그로 임시 취향을 만들어 게임픽 추천 엔진으로 추천. 결과에 취향 %(match)와 맞는 태그(reasons).
    장르를 좋아한다고 하면 그 장르 게임만 후보. 싫어하는 태그와 연관 태그(예: 캐주얼 → 귀여움)가 붙은 게임은 뺌.
    태그 이름은 종류 없이 이름만 (예: "퍼즐", "힐링", "RPG", "가챠", "광고"). 모르는 이름이면 쓸 수 있는 태그 목록을 돌려줌.

    Args:
        likes: 좋아하는 태그 이름들 (예: ["퍼즐", "힐링"])
        dislikes: 싫어하는 태그 이름들 (예: ["광고", "가챠"])
        favorite_game_ids: 재밌게 한 게임 id들 (그 게임 태그가 취향이 되고, 그 게임은 추천에서 뺌)
        limit: 최대 게임 수 (1~30)
    """
    with _conn() as conn:
        return tools.recommend_games(conn, likes, dislikes, favorite_game_ids, limit)


@mcp.tool(annotations=READ_ONLY)
def get_rankings(kind: str = "popular", tab: str = "전체", limit: int = 10) -> dict:
    """게임픽 랭킹.

    Args:
        kind: "popular" 인기 랭킹(스토어 무료 차트) | "likes" 추천 순위(게임픽 사용자 추천 수) | "monthly" 이달의 게임 | "surge" 인기 급상승
        tab: kind="popular"일 때만. "전체" | "Google Play" | "App Store" | 장르 탭("RPG", "시뮬레이션", "액션")
        limit: 최대 게임 수 (1~30)
    """
    with _conn() as conn:
        return tools.get_rankings(conn, kind, tab, limit)


if __name__ == "__main__":
    log.info("게임픽 MCP 서버 시작 (stdio, DB 읽기 전용)")
    mcp.run(transport="stdio")

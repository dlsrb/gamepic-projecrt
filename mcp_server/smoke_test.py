"""
게임픽 MCP 서버 연결 확인 (C13). Claude 데스크톱 없이, MCP Python SDK의 클라이언트로 서버를 stdio로 띄워
도구 목록을 받고 도구 4개를 한 번씩 불러 본다. 성공하면 마지막 줄에 "OK".

실행: python mcp_server/smoke_test.py
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SERVER = Path(__file__).resolve().parent / "gamepick_mcp.py"
CALLS = [
    ("search_games", {"query": "힐링 퍼즐", "limit": 3}),
    ("get_game", {"game_id": "stardew"}),
    ("recommend_games", {"likes": ["RPG"], "dislikes": ["가챠"], "limit": 3}),
    ("get_rankings", {"kind": "popular", "tab": "액션", "limit": 3}),
]


def _payload(result) -> dict:
    data = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if data is None:
        text = result.content[0].text
        data = json.loads(text)
    return data.get("result", data) if isinstance(data, dict) and set(data) == {"result"} else data


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        listed = await session.list_tools()
        names = [t.name for t in listed.tools]
        print("도구:", ", ".join(names))
        assert names and set(names) == {"search_games", "get_game", "recommend_games", "get_rankings"}, names
        for name, args in CALLS:
            data = _payload(await session.call_tool(name, args))
            games = data.get("games") or data.get("similar_games") or []
            first = data.get("name") or (games[0]["name"] if games else data.get("error"))
            print(f"{name}({args}) → {first}")
            assert "error" not in data, data
    print("OK")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())

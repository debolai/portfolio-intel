"""Health, MCP handshake, one tool call; optional LLM round-trip with --with-llm.

uv run python scripts/smoke.py http://localhost:8000 http://localhost:8001/mcp [--with-llm]
"""

import asyncio
import os
import sys

import httpx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

EXPECTED_TOOLS = {"get_active_exposures", "get_risk_contributions", "simulate_trades"}


async def main(api: str, mcp_url: str, with_llm: bool) -> None:
    hdr = {"Authorization": f"Bearer {os.environ['PI_MCP_TOKEN']}"}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{api}/healthz")
        r.raise_for_status()
        print("health ok, snapshot:", r.json().get("snapshot"))
    transport = streamable_http_client(mcp_url, http_client=create_mcp_http_client(headers=hdr))
    async with Client(transport) as s:
        names = {t.name for t in (await s.list_tools()).tools}
        assert not (EXPECTED_TOOLS - names), f"missing tools: {EXPECTED_TOOLS - names}"
        res = await s.call_tool("list_portfolios", {})
        assert not res.is_error, res
        print(f"mcp ok: {len(names)} tools, list_portfolios answered")
    if with_llm:
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(f"{api}/v1/ask", headers=hdr, json={"question": "List my portfolios"})
            r.raise_for_status()
            assert r.json()["grounding"]["ungrounded"] == [], r.json()
            print("llm ok")
    print("smoke test passed")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], "--with-llm" in sys.argv))

"""MCP server in-process (fixture snapshot): tool catalogue, read-only guarantees, schemas."""

import json
from pathlib import Path

import pytest
from mcp import Client

from portfolio_intel.mcp_server.server import mcp

pytestmark = pytest.mark.integration
SNAPSHOT = Path(__file__).parent / "tool_schemas.json"
FORBIDDEN = ("order", "execute", "submit", "trade_now", "buy", "sell", "place")


async def test_tools_listed_and_read_only():
    async with Client(mcp) as client:
        tools = (await client.list_tools()).tools
        names = {t.name for t in tools}
        assert "simulate_trades" in names
        assert not any(k in n for n in names for k in FORBIDDEN)
        assert all(t.annotations and t.annotations.read_only_hint for t in tools)
        assert all(t.annotations.destructive_hint is False for t in tools)


async def test_tool_schemas_match_snapshot():
    """Contract test: an accidental change to a tool's inputs or outputs fails CI.
    Regenerate deliberately with: UPDATE_SNAPSHOTS=1 uv run pytest -m integration"""
    async with Client(mcp) as client:
        tools = (await client.list_tools()).tools
    current = {t.name: {"input": t.input_schema, "output": t.output_schema} for t in tools}
    if not SNAPSHOT.exists() or __import__("os").environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOT.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
    assert current == json.loads(SNAPSHOT.read_text())


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("list_portfolios", {}),
        ("search_securities", {"query": "santander", "portfolio_id": "EQ_EU_PM"}),
        ("get_holdings", {"portfolio_id": "EQ_EU_PM", "top_n": 5, "group_by": "industry"}),
        ("get_active_exposures", {"portfolio_id": "EQ_EU_PM", "dimension": "industry",
                                  "filters": {"region": "Europe"}}),
        ("get_risk_summary", {"portfolio_id": "EQ_EU_PM"}),
        ("get_risk_contributions", {"portfolio_id": "EQ_EU_PM", "level": "sector"}),
        ("get_duration_profile", {"portfolio_id": "FI_US_PM"}),
        ("simulate_trades", {"portfolio_id": "EQ_EU_PM",
                             "trades": [{"security_id": "HSBA.L", "delta_weight_bps": -50}]}),
        ("get_methodology", {}),
    ],
)  # fmt: skip
async def test_every_tool_returns_structured_provenance(tool, args):
    async with Client(mcp) as client:
        r = await client.call_tool(tool, args)
    assert not r.is_error, r.content
    assert r.structured_content["provenance"]["calc_id"]


async def test_errors_are_reported_to_the_client():
    async with Client(mcp) as client:
        r = await client.call_tool(
            "simulate_trades",
            {"portfolio_id": "EQ_EU_PM",
             "trades": [{"security_id": "HSBA.L", "delta_weight_bps": 500}]},
        )  # fmt: skip
    assert r.is_error
    assert "pro_rata" in r.content[0].text


async def test_methodology_resource():
    async with Client(mcp) as client:
        res = await client.read_resource("methodology://risk")
    assert "Euler" in res.contents[0].text

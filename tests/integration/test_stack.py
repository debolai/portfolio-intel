"""Against the running Compose stack (make local-up): skipped when nothing is listening."""

import os

import httpx
import pytest
from mcp import Client

from portfolio_intel.agent.loop import mcp_client

pytestmark = pytest.mark.integration
API = os.environ.get("PI_API_URL", "http://localhost:8000")
MCP = os.environ.get("PI_MCP_URL_EXTERNAL", "http://localhost:8001/mcp")
TOKEN = os.environ.get("PI_MCP_TOKEN", "")


def _up() -> bool:
    try:
        return httpx.get(f"{API}/healthz", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


stack = pytest.mark.skipif(not _up(), reason="Compose stack not running")


@stack
def test_api_auth_and_analytics():
    assert httpx.get(f"{API}/v1/portfolios", timeout=10).status_code == 401
    r = httpx.get(f"{API}/v1/portfolios/EQ_EU_PM/risk",
                  headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30)  # fmt: skip
    assert r.status_code == 200, r.text
    assert r.json()["provenance"]["calc_id"]


@stack
async def test_mcp_over_http_matches_rest():
    client: Client = mcp_client(MCP, TOKEN)
    async with client as c:
        r = await c.call_tool("get_risk_summary", {"portfolio_id": "EQ_EU_PM"})
    rest = httpx.get(f"{API}/v1/portfolios/EQ_EU_PM/risk",
                     headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30).json()  # fmt: skip
    assert r.structured_content["tracking_error_pct"] == rest["tracking_error_pct"]
    assert r.structured_content["provenance"]["calc_id"] == rest["provenance"]["calc_id"]

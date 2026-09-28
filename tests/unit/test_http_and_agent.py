import json
from types import SimpleNamespace

import pytest
from mcp import Client
from starlette.testclient import TestClient

from portfolio_intel.agent import loop
from portfolio_intel.config import settings
from portfolio_intel.mcp_server.server import http_app, mcp
from portfolio_intel.telemetry import setup_tracing


def test_mcp_http_auth():
    with TestClient(http_app()) as client:  # context manager runs the MCP lifespan
        assert client.get("/healthz").json()["ok"] is True
        assert client.post("/mcp", json={}).status_code == 401
        r = client.post("/mcp", json={}, headers={"Authorization": f"Bearer {settings.mcp_token}"})
        assert r.status_code != 401


def test_setup_tracing_without_collector():
    setup_tracing("pi-test")  # no OTEL endpoint: must not fail or block


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool_use(name, args, id_="t1"):
    return SimpleNamespace(type="tool_use", name=name, input=args, id=id_)


def msg(content, stop):
    return SimpleNamespace(
        content=content, stop_reason=stop, usage=SimpleNamespace(input_tokens=1, output_tokens=1)
    )


class FakeClaude:
    """Scripted responses; records what the loop sent."""

    def __init__(self, script):
        self.script, self.sent = iter(script), []
        self.messages = SimpleNamespace(create=self.create)

    async def create(self, **kw):
        self.sent.append(kw)
        return next(self.script)


@pytest.mark.parametrize("invent", [False, True])
async def test_agent_loop_calls_tools_and_checks_grounding(monkeypatch, invent):
    bad = "Banks active weight is 99.99%."
    script = [
        msg([tool_use("get_active_exposures",
                      {"portfolio_id": "EQ_EU_PM", "dimension": "industry"})], "tool_use"),
        msg([text("placeholder")], "end_turn"),
        msg([text("Rewritten with tool numbers only.")], "end_turn"),
    ]  # fmt: skip
    fake = FakeClaude(script)
    monkeypatch.setattr(loop, "anthropic_client", lambda: fake)

    async with Client(mcp) as session:
        first = json.loads(
            (await session.call_tool("get_active_exposures",
                                     {"portfolio_id": "EQ_EU_PM", "dimension": "industry"}))
            .content[0].text
        )  # fmt: skip
        banks = next(r for r in first["rows"] if r["group"] == "Banks")["active_weight_pct"]
        script[1].content = [text(bad if invent else f"Banks active weight is {banks}.")]
        out = await loop.ask("What's my active weight in banks?", session=session)

    assert out["tool_calls"][0]["name"] == "get_active_exposures"
    assert out["grounding"]["ungrounded"] == []
    if invent:
        assert out["answer"] == "Rewritten with tool numbers only."
        assert fake.sent[-1]["tool_choice"] == {"type": "none"}
    else:
        assert str(banks) in out["answer"]
        assert len(fake.sent) == 2

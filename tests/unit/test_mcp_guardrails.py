import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from portfolio_intel.mcp_server import server
from portfolio_intel.mcp_server.guardrails import (
    EntitlementError,
    current_principal,
    validate_portfolio,
    validate_top_n,
)
from portfolio_intel.mcp_server.replay import find_event, replay
from portfolio_intel.mcp_server.sinks import JsonlSink, get_sink


def test_entitlements(svc):
    validate_portfolio(svc, "EQ_EU_PM")
    validate_portfolio(svc, "FI_US_BMK")  # benchmarks are public
    token = current_principal.set("someone-else")
    try:
        with pytest.raises(EntitlementError):
            validate_portfolio(svc, "EQ_EU_PM")
    finally:
        current_principal.reset(token)
    with pytest.raises(ValueError):
        validate_portfolio(svc, "NOPE")


def test_top_n_bounds():
    assert validate_top_n(500) == 50
    with pytest.raises(ValueError):
        validate_top_n(0)


def test_audit_record_and_replay(audit_path):
    res = server.get_active_exposures(portfolio_id="EQ_EU_PM", dimension="industry")
    rec = json.loads(audit_path.read_text().splitlines()[-1])
    assert rec["tool"] == "get_active_exposures"
    assert rec["status"] == "ok"
    assert rec["calc_id"] == res.provenance.calc_id
    assert rec["principal"] == "pm-demo"
    assert len(rec["trace_id"]) == 32
    ok, detail = replay(find_event(audit_path, rec["event_id"]))
    assert ok, detail


def test_expected_errors_become_tool_errors_and_are_audited(audit_path):
    with pytest.raises(ToolError, match="equities only"):
        server.get_risk_summary(portfolio_id="FI_US_PM")
    rec = json.loads(audit_path.read_text().splitlines()[-1])
    assert rec["status"] == "error"
    assert "equities only" in rec["error"]
    ok, detail = replay(rec)
    assert not ok and "failed" in detail


def test_simulate_trade_count_bound():
    trades = [server.Trade(security_id="HSBA.L", delta_weight_bps=1)] * 21
    with pytest.raises(ToolError, match="At most 20"):
        server.simulate_trades(portfolio_id="EQ_EU_PM", trades=trades)


def test_sinks(tmp_path):
    s = JsonlSink(tmp_path / "a" / "audit.jsonl")
    s.write({"x": 1})
    s.write({"x": 2})
    assert len((tmp_path / "a" / "audit.jsonl").read_text().splitlines()) == 2
    cfg = server.svc().cfg.model_copy(update={"audit_sink": "firehose", "audit_stream": None})
    with pytest.raises(ValueError):
        get_sink(cfg)

import pytest

from portfolio_intel.analytics.whatif import Trade, WhatIfError
from portfolio_intel.service import NotFoundError


def test_provenance_is_reproducible(svc):
    a = svc.get_risk_summary("EQ_EU_PM")
    b = svc.get_risk_summary("EQ_EU_PM")
    assert a == b
    assert a.provenance.calc_id == b.provenance.calc_id
    assert a.provenance.data_snapshot_id == svc.snapshot_id
    assert a.benchmark_id == "EQ_EU_BMK"  # defaulted from the portfolio table


def test_list_and_search(svc):
    ids = {p.portfolio_id for p in svc.list_portfolios().portfolios}
    assert ids == {"EQ_EU_BMK", "EQ_EU_PM", "EQ_EU_VGK", "FI_US_BMK", "FI_US_PM"}
    hits = svc.search_securities("hsbc", "EQ_EU_PM").matches
    assert [h.security_id for h in hits] == ["HSBA.L"]
    assert hits[0].portfolio_weight_pct is not None
    with pytest.raises(ValueError):
        svc.search_securities("  ")


def test_holdings_grouped(svc):
    h = svc.get_holdings("FI_US_PM", top_n=3, group_by="sector")
    assert len(h.top_holdings) == 3
    assert sum(g.weight_pct for g in h.groups) == pytest.approx(100.0, abs=1e-3)
    assert any(r.duration_years is not None for r in h.top_holdings)
    with pytest.raises(ValueError):
        svc.get_holdings("EQ_EU_PM", group_by="colour")


def test_exposures_sum_and_constituents(svc):
    e = svc.get_active_exposures("EQ_EU_PM", "industry")
    assert sum(r.active_weight_pct for r in e.rows) == pytest.approx(0.0, abs=1e-3)
    assert all(r.active_weight_pct > 0 for r in e.top_overweights)
    assert all(r.active_weight_pct < 0 for r in e.top_underweights)
    empty = svc.get_active_exposures("EQ_EU_PM", "industry", {"region": "Mars"})
    assert empty.provenance.warnings
    with pytest.raises(ValueError):
        svc.get_active_exposures("EQ_EU_PM", "colour")
    with pytest.raises(ValueError):
        svc.get_active_exposures("EQ_EU_PM", "sector", {"colour": "red"})


def test_risk_contributions_sum_to_te(svc):
    for level in ("security", "industry", "sector", "country"):
        rc = svc.get_risk_contributions("EQ_EU_PM", level, top_n=1000)
        assert sum(r.contribution_pct for r in rc.rows) == pytest.approx(
            rc.tracking_error_pct, abs=1e-3
        )
        assert sum(r.pct_of_te for r in rc.rows) == pytest.approx(100.0, abs=1e-2)


def test_risk_summary_real_fund_has_realised_te(svc):
    r = svc.get_risk_summary("EQ_EU_VGK")
    assert r.realised_tracking_error_pct is not None
    assert 0 < r.risk_coverage_pct <= 100


def test_equity_and_bond_tools_are_separated(svc):
    with pytest.raises(ValueError, match="equities only"):
        svc.get_risk_summary("FI_US_PM")
    with pytest.raises(ValueError, match="bond portfolios only"):
        svc.get_duration_profile("EQ_EU_PM")
    with pytest.raises(ValueError, match="is a benchmark"):
        svc.get_risk_summary("EQ_EU_BMK")
    with pytest.raises(NotFoundError):
        svc.get_risk_summary("NOPE")


def test_duration_profile(svc):
    d = svc.get_duration_profile("FI_US_PM", nav=100e6)
    assert d.active_duration_years == pytest.approx(
        d.duration_years - d.benchmark_duration_years, abs=1e-3
    )
    assert d.dv01 == pytest.approx(d.duration_years * 10_000, rel=1e-3)
    assert sum(r.contribution_to_active_duration_years for r in d.by_sector) == pytest.approx(
        d.active_duration_years, abs=1e-3
    )


def test_simulation_equity(svc):
    s = svc.simulate_trades("EQ_EU_PM", [Trade(security_id="HSBA.L", delta_weight_bps=-50)])
    assert s.hypothetical is True
    assert "No order" in s.note
    assert s.metrics["active_weight_industry:Banks"].delta == pytest.approx(-0.5)
    assert {"tracking_error", "beta", "portfolio_vol"} <= set(s.metrics)
    assert len(s.top_contribution_changes) == 5


def test_simulation_bond(svc):
    longest = max(
        svc.get_holdings("FI_US_PM", top_n=50).top_holdings,
        key=lambda h: h.duration_years or 0,
    )
    s = svc.simulate_trades(
        "FI_US_PM", [Trade(security_id=longest.security_id, delta_weight_bps=-50)]
    )
    assert s.metrics["duration"].delta < 0


def test_simulation_errors(svc):
    with pytest.raises(NotFoundError):
        svc.simulate_trades("EQ_EU_PM", [Trade(security_id="NOPE", delta_weight_bps=10)])
    with pytest.raises(WhatIfError):
        svc.simulate_trades("EQ_EU_PM", [Trade(security_id="HSBA.L", delta_weight_bps=500)])


def test_methodology_and_ewma(svc):
    assert "Tracking error" in svc.get_methodology().markdown
    cov, shrink = svc.covariance("ewma")
    assert shrink is None
    assert cov.shape[0] == cov.shape[1] > 10

import numpy as np
import pandas as pd
import pytest

from portfolio_intel.analytics.covariance import ewma_cov, ledoit_wolf_cov
from portfolio_intel.analytics.exposures import active_exposures
from portfolio_intel.analytics.fixed_income import duration_stats
from portfolio_intel.analytics.returns import base_currency_returns, realised_te
from portfolio_intel.analytics.whatif import Trade, WhatIfError, apply_trades


def test_fx_return_compounds():
    idx = pd.to_datetime(["2026-01-01", "2026-01-02"])
    prices = pd.DataFrame({"HSBA.L": [100.0, 101.0]}, index=idx)
    fx = pd.DataFrame({"GBP": [1.25, 1.275], "USD": [1.0, 1.0]}, index=idx)
    r, cov = base_currency_returns(prices, fx, pd.Series({"HSBA.L": "GBP"}), 1, 0.9)
    assert r["HSBA.L"].iloc[-1] == pytest.approx(0.0302)  # (1.01)(1.02) - 1
    assert cov["HSBA.L"] == 1.0


def test_low_coverage_security_is_dropped():
    idx = pd.date_range("2026-01-01", periods=11)
    prices = pd.DataFrame(
        {"A": np.linspace(100, 110, 11), "B": [np.nan] * 8 + [1.0, 1.1, 1.2]}, index=idx
    )
    fx = pd.DataFrame({"USD": 1.0}, index=idx)
    r, cov = base_currency_returns(prices, fx, pd.Series({"A": "USD", "B": "USD"}), 10, 0.9)
    assert list(r.columns) == ["A"]
    assert cov["B"] == pytest.approx(0.2)


def test_realised_te_needs_history():
    s = pd.Series(np.linspace(1, 2, 30))
    assert realised_te(s, s, 504) is None
    rng = np.random.default_rng(1)
    a = pd.Series(np.cumprod(1 + rng.normal(0, 0.01, 300)))
    b = pd.Series(np.cumprod(1 + rng.normal(0, 0.01, 300)))
    assert realised_te(a, b, 504) == pytest.approx(0.01 * np.sqrt(2) * np.sqrt(252), rel=0.15)


def test_covariances_are_annualised_and_psd():
    rng = np.random.default_rng(2)
    r = pd.DataFrame(rng.normal(0, 0.01, (500, 5)), columns=list("ABCDE"))
    cov, shrink = ledoit_wolf_cov(r)
    assert 0 <= shrink <= 1
    assert np.diag(cov).mean() == pytest.approx(0.0001 * 252, rel=0.15)
    assert np.linalg.eigvalsh(cov.to_numpy()).min() > 0
    e = ewma_cov(r)
    assert np.diag(e).mean() == pytest.approx(0.0001 * 252, rel=0.2)


def test_active_exposures_with_filter():
    attrs = pd.DataFrame(
        {"industry": ["Banks", "Banks", "Pharma", None], "region": ["Europe"] * 3 + ["Cash"]},
        index=["HSBA.L", "SAN.MC", "ROG.SW", "CASH"],
    )
    wp = pd.Series({"HSBA.L": 0.3, "SAN.MC": 0.2, "ROG.SW": 0.48, "CASH": 0.02})
    wb = pd.Series({"HSBA.L": 0.25, "SAN.MC": 0.15, "ROG.SW": 0.6})
    g = active_exposures(wp, wb, attrs, "industry", {"region": "europe"})
    assert g.loc["Banks", "active"] == pytest.approx(10.0)
    assert g.loc["Pharma", "active"] == pytest.approx(-12.0)
    assert "Unclassified" not in g.index  # cash filtered out by region
    g_all = active_exposures(wp, wb, attrs, "industry")
    assert g_all.loc["Unclassified", "active"] == pytest.approx(2.0)


def test_duration_three_bonds():
    d = pd.Series({"T10": 8.0, "T2": 1.9, "CORP": 6.0, "CASH": 0.0})
    wp = pd.Series({"T10": 0.5, "T2": 0.2, "CORP": 0.28, "CASH": 0.02})
    wb = pd.Series({"T10": 0.4, "T2": 0.3, "CORP": 0.3})
    s = duration_stats(wp, wb, d, nav=100e6)
    assert s.duration_port == pytest.approx(4.0 + 0.38 + 1.68)
    assert s.duration_bmk == pytest.approx(3.2 + 0.57 + 1.8)
    assert s.contrib.sum() == pytest.approx(s.active_duration)
    assert s.dv01_port == pytest.approx(s.duration_port * 10_000)


def test_trim_funded_from_cash():
    w = pd.Series({"A": 0.5, "B": 0.48, "CASH": 0.02})
    out = apply_trades(w, [Trade(security_id="A", delta_weight_bps=-50)])
    assert out["A"] == pytest.approx(0.495)
    assert out["CASH"] == pytest.approx(0.025)


def test_buy_needs_cash_or_pro_rata():
    w = pd.Series({"A": 0.5, "B": 0.49, "CASH": 0.01})
    with pytest.raises(WhatIfError, match="Insufficient cash"):
        apply_trades(w, [Trade(security_id="A", delta_weight_bps=200)])
    out = apply_trades(w, [Trade(security_id="A", delta_weight_bps=200)], funding="pro_rata")
    assert out.sum() == pytest.approx(1.0)
    assert out["CASH"] == pytest.approx(0.01)


def test_cannot_trim_more_than_held():
    w = pd.Series({"A": 0.001, "CASH": 0.999})
    with pytest.raises(WhatIfError, match="long-only"):
        apply_trades(w, [Trade(security_id="A", delta_weight_bps=-50)])


def test_trade_bounds():
    with pytest.raises(ValueError):
        Trade(security_id="A", delta_weight_bps=600)


def test_implausible_returns_are_treated_as_missing():
    idx = pd.date_range("2026-01-01", periods=5)
    prices = pd.DataFrame({"ROSE.L": [1.0, 1.01, 101.0, 1.02, 1.03]}, index=idx)
    fx = pd.DataFrame({"USD": 1.0}, index=idx)
    r, cov = base_currency_returns(prices, fx, pd.Series({"ROSE.L": "USD"}), 4, 0.0)
    assert r["ROSE.L"].abs().max() < 0.05  # the x100 print and its reversal are dropped
    assert cov["ROSE.L"] == pytest.approx(0.5)

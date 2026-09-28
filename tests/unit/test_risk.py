import numpy as np
import pandas as pd
import pytest

from portfolio_intel.analytics.risk import active_risk, group_contributions, total_risk_and_beta

COV = pd.DataFrame([[0.04, 0.006], [0.006, 0.09]], index=list("AB"), columns=list("AB"))


def test_two_asset_te_and_euler():
    r = active_risk(pd.Series({"A": 0.6, "B": 0.4}), pd.Series({"A": 0.5, "B": 0.5}), COV)
    # a'Σa = 0.1·0.0034 + (-0.1)·(-0.0084) = 0.00118
    assert r.te == pytest.approx(np.sqrt(0.00118), rel=1e-12)  # 3.4351%
    assert r.contrib["A"] == pytest.approx(0.00989778, rel=1e-6)
    assert r.contrib["B"] == pytest.approx(0.02445335, rel=1e-6)
    assert r.contrib.sum() == pytest.approx(r.te, rel=1e-12)
    assert (r.contrib / r.te)["B"] == pytest.approx(0.711864, rel=1e-5)  # B drives 71% of TE


def test_identical_portfolios_zero_te():
    w = pd.Series({"A": 0.5, "B": 0.5})
    assert active_risk(w, w, COV).te == 0.0


def test_cash_and_uncovered_names_reduce_coverage():
    wp = pd.Series({"A": 0.5, "B": 0.3, "X": 0.1, "CASH": 0.1})
    wb = pd.Series({"A": 0.5, "B": 0.5})
    r = active_risk(wp, wb, COV)
    # |a| inside cov: B 0.2; outside: X 0.1 (cash excluded from both)
    assert r.covered_active_abs == pytest.approx(0.2 / 0.3)
    assert r.active.sum() == pytest.approx(0.0)


def test_monte_carlo_matches_te():
    rng = np.random.default_rng(0)
    wp, wb = pd.Series({"A": 0.6, "B": 0.4}), pd.Series({"A": 0.5, "B": 0.5})
    sims = rng.multivariate_normal([0, 0], COV.to_numpy(), size=400_000)
    active = sims @ (wp - wb).to_numpy()
    assert active.std() == pytest.approx(active_risk(wp, wb, COV).te, rel=0.01)


def test_total_risk_and_beta():
    w = pd.Series({"A": 0.5, "B": 0.5})
    out = total_risk_and_beta(w, w, COV)
    assert out["beta"] == pytest.approx(1.0)
    assert out["vol_port"] == pytest.approx(np.sqrt(0.25 * 0.04 + 0.25 * 0.09 + 2 * 0.25 * 0.006))


def test_group_contributions_sum_members():
    c = pd.Series({"A": 0.01, "B": 0.02, "C": -0.005})
    g = group_contributions(c, pd.Series({"A": "Banks", "B": "Banks"}))
    assert g["Banks"] == pytest.approx(0.03)
    assert g["Unclassified"] == pytest.approx(-0.005)

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from portfolio_intel.analytics.risk import active_risk, group_contributions
from portfolio_intel.analytics.whatif import Trade, WhatIfError, apply_trades


def random_psd(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n * 3, n)) * 0.02
    return x.T @ x * 252


def _setup(n: int, seed: int) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    ids = [f"S{i}" for i in range(n)]
    cov = pd.DataFrame(random_psd(n, seed), index=ids, columns=ids)
    wp = pd.Series(rng.dirichlet(np.ones(n)), index=ids)
    wb = pd.Series(rng.dirichlet(np.ones(n)), index=ids)
    return wp, wb, cov


@given(n=st.integers(2, 30), seed=st.integers(0, 10_000))
@settings(max_examples=200, deadline=None)
def test_euler_contributions_sum_to_te(n, seed):
    wp, wb, cov = _setup(n, seed)
    r = active_risk(wp, wb, cov)
    assert np.isclose(r.contrib.sum(), r.te, rtol=1e-10, atol=1e-14)
    assert np.isclose(r.active.sum(), 0.0, atol=1e-12)


@given(n=st.integers(2, 20), seed=st.integers(0, 10_000), k=st.floats(0.1, 5.0))
@settings(max_examples=100, deadline=None)
def test_te_scales_with_active_weights(n, seed, k):
    wp, wb, cov = _setup(n, seed)
    base = active_risk(wp, wb, cov).te
    scaled = active_risk(wb + k * (wp - wb), wb, cov).te
    assert np.isclose(scaled, k * base, rtol=1e-9)


@given(n=st.integers(2, 20), seed=st.integers(0, 10_000))
@settings(max_examples=100, deadline=None)
def test_group_contributions_equal_member_sums(n, seed):
    wp, wb, cov = _setup(n, seed)
    r = active_risk(wp, wb, cov)
    groups = pd.Series({s: f"G{i % 3}" for i, s in enumerate(cov.index)})
    g = group_contributions(r.contrib, groups)
    assert np.isclose(g.sum(), r.te, rtol=1e-10)
    for name, members in groups.groupby(groups):
        assert np.isclose(g[name], r.contrib[members.index].sum())


@given(seed=st.integers(0, 10_000))
def test_zero_trade_is_identity(seed):
    rng = np.random.default_rng(seed)
    ids = ["A", "B", "C", "CASH"]
    w = pd.Series(rng.dirichlet(np.ones(4)), index=ids)
    out = apply_trades(w, [Trade(security_id="A", delta_weight_bps=0)])
    pd.testing.assert_series_equal(out.sort_index(), w.sort_index(), check_names=False)


@given(seed=st.integers(0, 10_000), bps=st.floats(-500, 500))
def test_pro_rata_keeps_weights_summing_to_one(seed, bps):
    rng = np.random.default_rng(seed)
    w = pd.Series(rng.dirichlet(np.ones(5)), index=["A", "B", "C", "D", "CASH"])
    try:
        out = apply_trades(w, [Trade(security_id="A", delta_weight_bps=bps)], funding="pro_rata")
    except WhatIfError:
        assert w["A"] + bps / 1e4 < 0
        return
    assert out.sum() == pytest.approx(1.0, abs=1e-9)

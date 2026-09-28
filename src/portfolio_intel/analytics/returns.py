"""Base-currency returns, coverage."""

import pandas as pd

# A one-day local return outside these bounds is treated as a data error (an unadjusted split,
# or a pence/pound flip on a London quote) and set to missing. Real moves this size exist
# (biotech trial results) but are rarer than bad prints; see docs/methodology.md.
MIN_DAILY_RETURN = -0.80
MAX_DAILY_RETURN = 2.00


def base_currency_returns(
    prices: pd.DataFrame, fx: pd.DataFrame, ccy: pd.Series, window: int, min_coverage: float
) -> tuple[pd.DataFrame, pd.Series]:
    """prices: date x security (local ccy). fx: date x currency (base per unit).
    ccy: security -> trading currency (the `currency` column, i.e. iShares' Market Currency).
    Returns (returns over covered securities, coverage per security).

    r_base = (1 + r_local)(1 + r_fx) - 1
    """
    idx = prices.index.union(fx.index)
    px = prices.reindex(idx).ffill(limit=3)  # bridge local holidays only
    fxr = fx.reindex(idx).ffill(limit=3).pct_change(fill_method=None)
    r_local = px.pct_change(fill_method=None)
    r_local = r_local.where((r_local >= MIN_DAILY_RETURN) & (r_local <= MAX_DAILY_RETURN))
    fx_aligned = fxr.reindex(columns=ccy.reindex(r_local.columns).to_numpy())
    fx_aligned.columns = r_local.columns
    r = (1 + r_local) * (1 + fx_aligned.fillna(0.0)) - 1
    r = r.iloc[-window:]
    coverage = r.notna().mean()
    keep = coverage[coverage >= min_coverage].index
    return r[keep].fillna(0.0), coverage


def realised_te(etf_a: pd.Series, etf_b: pd.Series, window: int) -> float | None:
    """Ex-post TE: annualised stdev of the daily return difference of two funds (USD prices)."""
    df = pd.concat([etf_a, etf_b], axis=1).dropna()
    r = df.pct_change().dropna().iloc[-window:]
    if len(r) < 60:
        return None
    diff = r.iloc[:, 0] - r.iloc[:, 1]
    return float(diff.std() * 252**0.5)

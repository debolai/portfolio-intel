"""Data-quality checks (blueprint 2.8). Errors stop the ingest; everything else is recorded."""

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Issue:
    check_name: str
    portfolio_id: str
    security_id: str
    severity: str  # error | warn | info
    detail: str


class DataQualityError(RuntimeError):
    def __init__(self, issues: list[Issue]):
        self.issues = issues
        lines = "\n".join(f"  {i.check_name} [{i.portfolio_id}] {i.detail}" for i in issues)
        super().__init__(f"{len(issues)} data-quality error(s):\n{lines}")


def to_frame(issues: list[Issue]) -> pd.DataFrame:
    cols = ["check_name", "portfolio_id", "security_id", "severity", "detail"]
    return pd.DataFrame([asdict(i) for i in issues], columns=cols)


def raise_on_errors(issues: list[Issue]) -> None:
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise DataQualityError(errors)


def check_weights(holdings: pd.DataFrame) -> list[Issue]:
    """holdings: portfolio_id, security_id, weight."""
    out = []
    for pid, g in holdings.groupby("portfolio_id"):
        off = abs(float(g["weight"].sum()) - 1.0)
        sev = "error" if off > 0.02 else "warn" if off > 0.005 else ""
        if sev:
            out.append(Issue("weights_sum", str(pid), "", sev, f"weights sum off by {off:.4%}"))
        for sid in g.loc[g["security_id"].duplicated(), "security_id"]:
            out.append(Issue("duplicate_security", str(pid), str(sid), "error", "duplicate id"))
    return out


def check_unmapped(
    portfolio_id: str, unmapped: pd.DataFrame, total_weight: float = 1.0
) -> list[Issue]:
    """Rows that could not be given a security_id (e.g. unmapped exchange)."""
    if unmapped.empty:
        return []
    w = float(unmapped["weight"].sum()) / total_weight
    out = [
        Issue("unmapped_symbol", portfolio_id, "", "error" if w > 0.03 else "warn",
              f"{len(unmapped)} rows, {w:.3%} of weight without security_id")
    ]  # fmt: skip
    out += [
        Issue("unmapped_symbol", portfolio_id, "", "info", f"{r.ticker} on {r.exchange}")
        for r in unmapped.itertuples()
    ]
    return out


def price_coverage(prices: pd.DataFrame, window: int) -> pd.Series:
    """Share of the last `window` trading days with a price, per symbol (wide prices)."""
    return prices.iloc[-window:].notna().mean()


def check_price_coverage(
    portfolio_id: str, weights: pd.Series, coverage: pd.Series, min_coverage: float
) -> list[Issue]:
    """weights: security_id -> weight for the benchmark's equities (cash excluded)."""
    w = weights.drop("CASH", errors="ignore")
    covered = coverage.reindex(w.index).fillna(0.0) >= min_coverage
    share = float(w[covered].sum() / w.sum())
    sev = "error" if share < 0.90 else "warn" if share < 0.97 else "info"
    out = [Issue("price_coverage", portfolio_id, "", sev, f"{share:.2%} of weight priced")]
    for sid in w[~covered].sort_values(ascending=False).index[:25]:
        out.append(
            Issue("price_coverage", portfolio_id, str(sid), "info",
                  f"coverage {coverage.get(sid, 0.0):.0%}, weight {w[sid]:.3%}")
        )  # fmt: skip
    return out


def check_prices(prices: pd.DataFrame) -> list[Issue]:
    """Stale runs (> 5 identical closes in a row) and |daily return| > 40%."""
    out = []
    for sid in prices.columns:
        s = prices[sid].dropna()
        if s.empty:
            continue
        run_id = (s != s.shift()).cumsum()
        longest = int(s.groupby(run_id).size().max())
        if longest > 5:
            out.append(Issue("stale_prices", "", str(sid), "warn", f"{longest} identical closes"))
        r = s.pct_change().abs()
        if (r > 0.40).any():
            day = r.idxmax()
            out.append(
                Issue(
                    "return_outlier", "", str(sid), "warn", f"{r.max():.0%} move on {day:%Y-%m-%d}"
                )
            )
    return out


def check_industry(portfolio_id: str, weights: pd.Series, industry: pd.Series) -> list[Issue]:
    w = weights.drop("CASH", errors="ignore")
    missing = industry.reindex(w.index).isna()
    share = float(w[missing].sum())
    if share > 0.02:
        return [Issue("missing_industry", portfolio_id, "", "warn", f"{share:.2%} of weight")]
    return []


def check_durations(portfolio_id: str, bonds: pd.DataFrame) -> list[Issue]:
    """bonds: security_id, duration for non-cash bond lines."""
    bad = bonds[bonds["duration"].isna() | ~np.isfinite(bonds["duration"].astype(float))]
    return [
        Issue("missing_duration", portfolio_id, str(sid), "error", "bond without duration")
        for sid in bad["security_id"]
    ]


def check_excluded(portfolio_id: str, excluded: pd.DataFrame) -> list[Issue]:
    return [
        Issue("excluded_rows", portfolio_id, "", "info",
              f"{reason}: {len(g)} rows, {g['weight'].sum():.4%} of gross value")
        for reason, g in excluded.groupby("excluded_reason")
    ]  # fmt: skip


def check_vgk_resolution(resolved: pd.DataFrame, failed_symbols: set[str]) -> list[Issue]:
    """Unresolved rows and country-default symbols that failed to price."""
    out = []
    live = resolved[resolved["excluded_reason"] == ""]
    unres = live[live["security_id"].isna()]
    if len(unres):
        w = float(unres["weight"].sum())
        out.append(
            Issue("vgk_unresolved", "EQ_EU_VGK", "", "error" if w > 0.01 else "warn",
                  f"{len(unres)} rows, {w:.3%} of weight")
        )  # fmt: skip
    cd = live[(live["id_source"] == "country_default") & live["security_id"].isin(failed_symbols)]
    if len(cd):
        w = float(cd["weight"].sum())
        out.append(
            Issue("vgk_country_default_unpriced", "EQ_EU_VGK", "", "error" if w > 0.01 else "warn",
                  f"{len(cd)} rows, {w:.3%} of weight: " + ", ".join(cd["security_id"][:15]))
        )  # fmt: skip
    return out

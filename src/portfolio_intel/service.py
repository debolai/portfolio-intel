"""Shared façade used by MCP + REST. Loads one snapshot, caches covariance, one method per tool."""

import hashlib
import json
import logging
import tempfile
from functools import cached_property
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from opentelemetry import trace

from portfolio_intel.analytics import models as m
from portfolio_intel.analytics.covariance import ewma_cov, ledoit_wolf_cov
from portfolio_intel.analytics.exposures import active_exposures, weights_frame
from portfolio_intel.analytics.fixed_income import duration_stats
from portfolio_intel.analytics.returns import base_currency_returns, realised_te
from portfolio_intel.analytics.risk import (
    active_risk,
    group_contributions,
    total_risk_and_beta,
)
from portfolio_intel.analytics.whatif import Trade, apply_trades
from portfolio_intel.config import Settings, settings
from portfolio_intel.data.store import open_readonly

log = logging.getLogger(__name__)
tracer = trace.get_tracer("portfolio_intel.analytics")

DIMENSIONS = ("sector", "industry", "industry_group", "country", "region", "currency")
LEVELS = ("security", "industry", "sector", "country")
METHODOLOGY = Path(__file__).parent / "methodology.md"
LOW_COVERAGE = 0.95


class NotFoundError(ValueError): ...


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def r4(x: float) -> float:
    return round(float(x), 4)


class PortfolioService:
    def __init__(self, db_path: str | Path, cfg: Settings = settings):
        self.cfg = cfg
        self.db_path = str(db_path)
        con = open_readonly(self.db_path)
        try:
            snap = con.execute("SELECT * FROM snapshot").df().iloc[0]
            self.security = con.execute("SELECT * FROM security").df().set_index("security_id")
            self.portfolio = con.execute("SELECT * FROM portfolio").df().set_index("portfolio_id")
            self.holding = con.execute("SELECT * FROM holding").df()
            self.price = con.execute("SELECT date, security_id, close FROM price").df()
            self.fx = con.execute("SELECT * FROM fx").df()
        finally:
            con.close()
        self.snapshot_id = str(snap["snapshot_id"])
        self.as_of = str(pd.Timestamp(snap["as_of"]).date())
        self.methodology_version = str(snap["methodology_version"])
        self._cov: dict[str, tuple[pd.DataFrame, float | None]] = {}

    @classmethod
    def from_settings(cls, cfg: Settings = settings) -> "PortfolioService":
        """Open PI_DUCKDB_PATH, or the snapshot named in s3://PI_DATA_BUCKET/snapshots/LATEST."""
        if cfg.data_bucket:
            import boto3

            s3 = boto3.client("s3")
            key = cfg.snapshot_key or (
                s3.get_object(Bucket=cfg.data_bucket, Key="snapshots/LATEST")["Body"]
                .read()
                .decode()
                .strip()
            )
            local = Path(tempfile.gettempdir()) / "portfolio.duckdb"
            s3.download_file(cfg.data_bucket, key, str(local))
            log.info("loaded s3://%s/%s", cfg.data_bucket, key)
            return cls(local, cfg)
        return cls(cfg.duckdb_path, cfg)

    # ------------------------------------------------------------------ helpers

    def _check_portfolio(self, pid: str) -> None:
        if pid not in self.portfolio.index:
            raise NotFoundError(
                f"Unknown portfolio {pid!r}. Known: {', '.join(self.portfolio.index)}"
            )

    def benchmark_of(self, pid: str, benchmark_id: str | None) -> str:
        self._check_portfolio(pid)
        bmk = benchmark_id or self.portfolio.loc[pid, "benchmark_id"]
        if not isinstance(bmk, str) or not bmk:
            raise ValueError(f"{pid} is a benchmark; pass benchmark_id to compare it to another")
        self._check_portfolio(bmk)
        return bmk

    def weights(self, pid: str) -> pd.Series:
        self._check_portfolio(pid)
        h = self.holding[self.holding["portfolio_id"] == pid]
        return h.set_index("security_id")["weight"].astype(float)

    def is_bond(self, pid: str) -> bool:
        return pid.startswith("FI_")

    def provenance(
        self,
        tool: str,
        args: dict[str, Any],
        warnings: list[str] | None = None,
        shrinkage: float | None = None,
        method: str | None = None,
    ) -> m.Provenance:
        raw = tool + canonical_json(args) + self.snapshot_id + self.methodology_version
        return m.Provenance(
            as_of=self.as_of,
            data_snapshot_id=self.snapshot_id,
            methodology_version=self.methodology_version,
            calc_id=hashlib.sha256(raw.encode()).hexdigest()[:16],
            covariance_method=method or f"ledoit_wolf_daily_{self.cfg.risk_window_days}d",
            shrinkage=None if shrinkage is None else r4(shrinkage),
            warnings=warnings or [],
        )

    def name(self, sid: str) -> str:
        return str(self.security["name"].get(sid, sid))

    @cached_property
    def returns(self) -> pd.DataFrame:
        """Base-currency daily returns for every equity with enough history."""
        with tracer.start_as_current_span("analytics.returns") as span:
            eq = self.security[self.security["asset_class"] == "Equity"]
            px = self.price.pivot_table(
                index="date", columns="security_id", values="close", aggfunc="last"
            )
            px = px[[c for c in px.columns if c in eq.index]]
            px.index = pd.to_datetime(px.index)
            fx = self.fx.pivot_table(
                index="date", columns="currency", values="base_per_unit", aggfunc="last"
            )
            fx.index = pd.to_datetime(fx.index)
            r, _ = base_currency_returns(
                px,
                fx,
                eq["currency"],
                self.cfg.risk_window_days,
                self.cfg.min_price_coverage,
            )
            span.set_attribute("n_securities", r.shape[1])
            return r

    def covariance(self, method: str = "ledoit_wolf") -> tuple[pd.DataFrame, float | None]:
        if method not in self._cov:
            with tracer.start_as_current_span("analytics.covariance") as span:
                span.set_attribute("method", method)
                span.set_attribute("n_securities", self.returns.shape[1])
                if method == "ledoit_wolf":
                    cov, shrink = ledoit_wolf_cov(self.returns)
                    self._cov[method] = (cov, shrink)
                else:
                    self._cov[method] = (ewma_cov(self.returns), None)
        return self._cov[method]

    def _require_equity(self, pid: str) -> None:
        if self.is_bond(pid):
            raise ValueError(
                "The risk model covers equities only. For bond portfolios use "
                "get_duration_profile (duration, active duration, DV01)."
            )

    def _require_bond(self, pid: str) -> None:
        if not self.is_bond(pid):
            raise ValueError("Duration is available for bond portfolios only (e.g. FI_US_PM).")

    # ------------------------------------------------------------------ tools

    def list_portfolios(self) -> m.PortfolioList:
        rows = []
        for pid, p in self.portfolio.iterrows():
            h = self.holding[self.holding["portfolio_id"] == pid]
            rows.append(
                m.PortfolioInfo(
                    portfolio_id=str(pid),
                    name=str(p["name"]),
                    kind=str(p["kind"]),
                    benchmark_id=p["benchmark_id"] if isinstance(p["benchmark_id"], str) else None,
                    n_holdings=len(h),
                    holdings_as_of=str(pd.Timestamp(h["as_of"].max()).date()),
                )
            )
        return m.PortfolioList(portfolios=rows, provenance=self.provenance("list_portfolios", {}))

    def search_securities(
        self, query: str, portfolio_id: str | None = None, limit: int = 10
    ) -> m.SecuritySearchResult:
        q = query.strip().casefold()
        if not q:
            raise ValueError("query must not be empty")
        sec = self.security
        hay = sec.index.to_series().str.casefold() + " " + sec["name"].fillna("").str.casefold()
        hay = hay + " " + sec["ticker"].fillna("").str.casefold()
        hits = sec[hay.str.contains(q, regex=False)]
        w = None
        if portfolio_id is not None:
            w = self.weights(portfolio_id)
            bmk = self.portfolio.loc[portfolio_id, "benchmark_id"]
            universe = set(w.index) | (
                set(self.weights(bmk).index) if isinstance(bmk, str) else set()
            )
            hits = hits[hits.index.isin(universe)]
        matches = [
            m.SecurityMatch(
                security_id=str(sid),
                name=str(s["name"]),
                asset_class=str(s["asset_class"]),
                country=s["country"] if isinstance(s["country"], str) else None,
                industry=s["industry"] if isinstance(s["industry"], str) else None,
                portfolio_weight_pct=r4(w.get(sid, 0.0) * 100) if w is not None else None,
            )
            for sid, s in hits.head(limit).iterrows()
        ]
        args = {"query": query, "portfolio_id": portfolio_id}
        return m.SecuritySearchResult(
            query=query, matches=matches, provenance=self.provenance("search_securities", args)
        )

    def get_holdings(
        self, portfolio_id: str, top_n: int = 10, group_by: str | None = None
    ) -> m.HoldingsResult:
        w = self.weights(portfolio_id).sort_values(ascending=False)
        attrs = self.security.reindex(w.index)
        top = [
            m.HoldingRow(
                security_id=str(sid),
                name=self.name(sid),
                weight_pct=r4(w[sid] * 100),
                sector=_s(attrs.loc[sid, "sector"]),
                industry=_s(attrs.loc[sid, "industry"]),
                country=_s(attrs.loc[sid, "country"]),
                duration_years=_f(attrs.loc[sid, "duration"]),
                maturity=_s(attrs.loc[sid, "maturity"]),
            )
            for sid in w.index[:top_n]
        ]
        groups = None
        if group_by:
            if group_by not in DIMENSIONS:
                raise ValueError(f"group_by must be one of {DIMENSIONS}")
            g = pd.DataFrame({"w": w, "k": attrs[group_by].fillna("Unclassified")}).groupby("k")
            agg = g["w"].agg(["sum", "size"]).sort_values("sum", ascending=False)
            groups = [
                m.GroupRow(group=str(k), weight_pct=r4(r["sum"] * 100), n_holdings=int(r["size"]))
                for k, r in agg.iterrows()
            ]
        args = {"portfolio_id": portfolio_id, "top_n": top_n, "group_by": group_by}
        return m.HoldingsResult(
            portfolio_id=portfolio_id,
            n_holdings=len(w),
            top_holdings=top,
            groups=groups,
            group_by=group_by,
            provenance=self.provenance("get_holdings", args),
        )

    def get_active_exposures(
        self,
        portfolio_id: str,
        dimension: str = "sector",
        filters: dict[str, str] | None = None,
        benchmark_id: str | None = None,
        top_n: int = 5,
    ) -> m.ExposureResult:
        if dimension not in DIMENSIONS:
            raise ValueError(f"dimension must be one of {DIMENSIONS}")
        for col in filters or {}:
            if col not in DIMENSIONS:
                raise ValueError(f"filter keys must be among {DIMENSIONS}")
        bmk = self.benchmark_of(portfolio_id, benchmark_id)
        wp, wb = self.weights(portfolio_id), self.weights(bmk)
        with tracer.start_as_current_span("analytics.exposures"):
            g = active_exposures(wp, wb, self.security, dimension, filters)
            detail = weights_frame(wp, wb, self.security, filters)
        detail = detail[detail.index != "CASH"]
        rows = [
            m.ExposureRow(
                group=str(k),
                portfolio_weight_pct=r4(r["port"]),
                benchmark_weight_pct=r4(r["bmk"]),
                active_weight_pct=r4(r["active"]),
            )
            for k, r in g.iterrows()
        ]

        def constituents(frame: pd.DataFrame) -> list[m.ConstituentRow]:
            return [
                m.ConstituentRow(
                    security_id=str(sid),
                    name=self.name(str(sid)),
                    group=_s(r[dimension]) or "Unclassified",
                    portfolio_weight_pct=r4(r["port"] * 100),
                    benchmark_weight_pct=r4(r["bmk"] * 100),
                    active_weight_pct=r4(r["active"] * 100),
                )
                for sid, r in frame.iterrows()
            ]

        over = detail[detail["active"] > 0].nlargest(top_n, "active")
        under = detail[detail["active"] < 0].nsmallest(top_n, "active")
        args = {"portfolio_id": portfolio_id, "dimension": dimension,
                "filters": filters or {}, "benchmark_id": bmk}  # fmt: skip
        warnings = []
        if filters and detail.empty:
            warnings.append(f"No securities match filters {filters}.")
        return m.ExposureResult(
            portfolio_id=portfolio_id,
            benchmark_id=bmk,
            dimension=dimension,
            filters=filters or {},
            rows=rows,
            top_overweights=constituents(over),
            top_underweights=constituents(under),
            provenance=self.provenance("get_active_exposures", args, warnings),
        )

    def _risk_warnings(self, covered: float) -> list[str]:
        if covered < LOW_COVERAGE:
            return [
                f"Only {covered:.1%} of absolute active weight is in the risk model "
                "(short price history); tracking error is likely understated."
            ]
        return []

    def get_risk_summary(self, portfolio_id: str, benchmark_id: str | None = None) -> m.RiskSummary:
        self._require_equity(portfolio_id)
        bmk = self.benchmark_of(portfolio_id, benchmark_id)
        cov, shrink = self.covariance()
        wp, wb = self.weights(portfolio_id), self.weights(bmk)
        with tracer.start_as_current_span("analytics.active_risk") as span:
            ar = active_risk(wp, wb, cov)
            tr = total_risk_and_beta(wp, wb, cov)
            span.set_attribute("coverage", ar.covered_active_abs)
        realised = self._realised_te(portfolio_id, bmk)
        args = {"portfolio_id": portfolio_id, "benchmark_id": bmk}
        return m.RiskSummary(
            portfolio_id=portfolio_id,
            benchmark_id=bmk,
            tracking_error_pct=r4(ar.te * 100),
            portfolio_vol_pct=r4(tr["vol_port"] * 100),
            benchmark_vol_pct=r4(tr["vol_bmk"] * 100),
            beta=r4(tr["beta"]),
            realised_tracking_error_pct=None if realised is None else r4(realised * 100),
            risk_coverage_pct=r4(ar.covered_active_abs * 100),
            n_securities_in_model=int(cov.shape[0]),
            provenance=self.provenance(
                "get_risk_summary", args, self._risk_warnings(ar.covered_active_abs), shrink
            ),
        )

    def _realised_te(self, pid: str, bmk: str) -> float | None:
        a, b = self.portfolio.loc[pid, "etf_symbol"], self.portfolio.loc[bmk, "etf_symbol"]
        if not (isinstance(a, str) and isinstance(b, str)):
            return None
        px = self.price[self.price["security_id"].isin([a, b])]
        wide = px.pivot_table(index="date", columns="security_id", values="close", aggfunc="last")
        if a not in wide or b not in wide:
            return None
        return realised_te(wide[a], wide[b], self.cfg.risk_window_days)

    def get_risk_contributions(
        self,
        portfolio_id: str,
        level: str = "security",
        top_n: int = 10,
        benchmark_id: str | None = None,
    ) -> m.RiskContributions:
        if level not in LEVELS:
            raise ValueError(f"level must be one of {LEVELS}")
        self._require_equity(portfolio_id)
        bmk = self.benchmark_of(portfolio_id, benchmark_id)
        cov, shrink = self.covariance()
        with tracer.start_as_current_span("analytics.active_risk"):
            ar = active_risk(self.weights(portfolio_id), self.weights(bmk), cov)
        active = ar.active.reindex(cov.index, fill_value=0.0)
        if level == "security":
            contrib, act = ar.contrib, active
        else:
            groups = self.security[level]
            contrib = group_contributions(ar.contrib, groups)
            act = group_contributions(ar.active.drop("CASH", errors="ignore"), groups)
        contrib = contrib[contrib.abs() > 0].sort_values(key=abs, ascending=False)
        te = ar.te
        rows = [
            m.ContributionRow(
                group=str(k),
                name=self.name(str(k)) if level == "security" else None,
                active_weight_pct=r4(act.get(k, 0.0) * 100),
                contribution_pct=r4(c * 100),
                pct_of_te=r4(c / te * 100) if te else 0.0,
            )
            for k, c in contrib.head(top_n).items()
        ]
        args = {"portfolio_id": portfolio_id, "level": level, "top_n": top_n, "benchmark_id": bmk}
        return m.RiskContributions(
            portfolio_id=portfolio_id,
            benchmark_id=bmk,
            level=level,
            tracking_error_pct=r4(te * 100),
            rows=rows,
            n_total=len(contrib),
            provenance=self.provenance(
                "get_risk_contributions", args, self._risk_warnings(ar.covered_active_abs), shrink
            ),
        )

    def get_duration_profile(
        self, portfolio_id: str, benchmark_id: str | None = None, nav: float = 100e6
    ) -> m.DurationProfile:
        self._require_bond(portfolio_id)
        bmk = self.benchmark_of(portfolio_id, benchmark_id)
        wp, wb = self.weights(portfolio_id), self.weights(bmk)
        dur = self.security["duration"].astype(float)
        ds = duration_stats(wp, wb, dur, nav)
        sectors = self.security["sector"].reindex(ds.contrib.index).fillna("Unclassified")
        frame = (
            pd.DataFrame(
                {
                    "port": wp.reindex(ds.contrib.index, fill_value=0.0),
                    "bmk": wb.reindex(ds.contrib.index, fill_value=0.0),
                    "contrib": ds.contrib,
                    "sector": sectors,
                }
            )
            .groupby("sector")[["port", "bmk", "contrib"]]
            .sum()
        )
        frame = frame.sort_values("contrib", key=abs, ascending=False)
        by_sector = [
            m.DurationRow(
                group=str(k),
                portfolio_weight_pct=r4(r["port"] * 100),
                benchmark_weight_pct=r4(r["bmk"] * 100),
                contribution_to_active_duration_years=r4(r["contrib"]),
            )
            for k, r in frame.iterrows()
        ]
        args = {"portfolio_id": portfolio_id, "benchmark_id": bmk, "nav": nav}
        return m.DurationProfile(
            portfolio_id=portfolio_id,
            benchmark_id=bmk,
            duration_years=r4(ds.duration_port),
            benchmark_duration_years=r4(ds.duration_bmk),
            active_duration_years=r4(ds.active_duration),
            nav=nav,
            dv01=round(ds.dv01_port, 2),
            benchmark_dv01=round(ds.dv01_bmk, 2),
            by_sector=by_sector,
            provenance=self.provenance("get_duration_profile", args),
        )

    def simulate_trades(
        self,
        portfolio_id: str,
        trades: list[Trade],
        funding: Literal["cash", "pro_rata"] = "cash",
        benchmark_id: str | None = None,
    ) -> m.SimulationResult:
        bmk = self.benchmark_of(portfolio_id, benchmark_id)
        unknown = [t.security_id for t in trades if t.security_id not in self.security.index]
        if unknown:
            raise NotFoundError(f"Unknown security_id(s) {unknown}; use search_securities first.")
        before = self.weights(portfolio_id)
        wb = self.weights(bmk)
        with tracer.start_as_current_span("analytics.whatif") as span:
            span.set_attribute("n_trades", len(trades))
            after = apply_trades(before, trades, funding)
            metrics: dict[str, m.Metric] = {}
            changes: list[m.ContributionChange] = []
            warnings: list[str] = []
            traded = [t.security_id for t in trades]

            def metric(b: float, a: float, unit: str) -> m.Metric:
                return m.Metric(before=r4(b), after=r4(a), delta=r4(a - b), unit=unit)

            for dim in ("industry", "sector"):
                for grp in {_s(self.security.loc[s, dim]) for s in traded} - {None}:
                    ids = self.security.index[self.security[dim] == grp]
                    wb_g = wb.reindex(ids, fill_value=0.0).sum()
                    b = before.reindex(ids, fill_value=0.0).sum() - wb_g
                    a = after.reindex(ids, fill_value=0.0).sum() - wb_g
                    metrics[f"active_weight_{dim}:{grp}"] = metric(b * 100, a * 100, m.PP_NAV)

            if self.is_bond(portfolio_id):
                dur = self.security["duration"].astype(float)
                d0 = duration_stats(before, wb, dur, 100e6)
                d1 = duration_stats(after, wb, dur, 100e6)
                metrics["duration"] = metric(d0.duration_port, d1.duration_port, "years")
                metrics["active_duration"] = metric(d0.active_duration, d1.active_duration, "years")
            else:
                cov, _ = self.covariance()
                r0, r1 = active_risk(before, wb, cov), active_risk(after, wb, cov)
                b0, b1 = total_risk_and_beta(before, wb, cov), total_risk_and_beta(after, wb, cov)
                metrics["tracking_error"] = metric(r0.te * 100, r1.te * 100, "annualised %")
                metrics["beta"] = metric(b0["beta"], b1["beta"], "unitless")
                metrics["portfolio_vol"] = metric(
                    b0["vol_port"] * 100, b1["vol_port"] * 100, "annualised %"
                )
                delta = (r1.contrib - r0.contrib).sort_values(key=np.abs, ascending=False)
                changes = [
                    m.ContributionChange(
                        security_id=str(sid),
                        name=self.name(str(sid)),
                        contribution_before_pct=r4(r0.contrib.get(str(sid), 0.0) * 100),
                        contribution_after_pct=r4(r1.contrib.get(str(sid), 0.0) * 100),
                        delta_pct=r4(d * 100),
                    )
                    for sid, d in delta.head(5).items()
                ]
                missing = [s for s in traded if s not in cov.index]
                if missing:
                    warnings.append(
                        f"{missing} not in the risk model (short price history); "
                        "their risk impact is not captured."
                    )
        args = {"portfolio_id": portfolio_id, "trades": [t.model_dump() for t in trades],
                "funding": funding, "benchmark_id": bmk}  # fmt: skip
        return m.SimulationResult(
            portfolio_id=portfolio_id,
            benchmark_id=bmk,
            trades=[t.model_dump() for t in trades],
            funding=funding,
            metrics=metrics,
            top_contribution_changes=changes,
            provenance=self.provenance("simulate_trades", args, warnings),
        )

    def methodology_markdown(self) -> str:
        return METHODOLOGY.read_text() if METHODOLOGY.exists() else "Methodology not bundled."

    def get_methodology(self) -> m.MethodologyResult:
        return m.MethodologyResult(
            markdown=self.methodology_markdown(), provenance=self.provenance("get_methodology", {})
        )


def _s(x: object) -> str | None:
    if x is None or (isinstance(x, float) and np.isnan(x)) or x is pd.NaT:
        return None
    return str(x)


def _f(x: object) -> float | None:
    if isinstance(x, int | float) and not np.isnan(x):
        return r4(x)
    return None

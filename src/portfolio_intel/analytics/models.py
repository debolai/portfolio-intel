"""Pydantic result models. Every number is already in display units with an explicit unit."""

from pydantic import BaseModel, Field

PP_NAV = "percentage points of NAV"
PP_TE = "percentage points of annualised TE"


class Provenance(BaseModel):
    as_of: str
    data_snapshot_id: str
    methodology_version: str
    calc_id: str
    covariance_method: str = "ledoit_wolf_daily_504d"
    shrinkage: float | None = None
    warnings: list[str] = Field(default_factory=list)


class PortfolioInfo(BaseModel):
    portfolio_id: str
    name: str
    kind: str
    benchmark_id: str | None
    n_holdings: int
    holdings_as_of: str


class PortfolioList(BaseModel):
    portfolios: list[PortfolioInfo]
    provenance: Provenance


class SecurityMatch(BaseModel):
    security_id: str
    name: str
    asset_class: str
    country: str | None
    industry: str | None
    portfolio_weight_pct: float | None = None
    unit: str = PP_NAV


class SecuritySearchResult(BaseModel):
    query: str
    matches: list[SecurityMatch]
    provenance: Provenance


class HoldingRow(BaseModel):
    security_id: str
    name: str
    weight_pct: float
    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    duration_years: float | None = None
    maturity: str | None = None
    unit: str = PP_NAV


class GroupRow(BaseModel):
    group: str
    weight_pct: float
    n_holdings: int
    unit: str = PP_NAV


class HoldingsResult(BaseModel):
    portfolio_id: str
    n_holdings: int
    top_holdings: list[HoldingRow]
    groups: list[GroupRow] | None = None
    group_by: str | None = None
    provenance: Provenance


class ExposureRow(BaseModel):
    group: str
    portfolio_weight_pct: float
    benchmark_weight_pct: float
    active_weight_pct: float
    unit: str = PP_NAV


class ConstituentRow(BaseModel):
    security_id: str
    name: str
    group: str
    portfolio_weight_pct: float
    benchmark_weight_pct: float
    active_weight_pct: float
    unit: str = PP_NAV


class ExposureResult(BaseModel):
    portfolio_id: str
    benchmark_id: str
    dimension: str
    filters: dict[str, str]
    rows: list[ExposureRow]
    top_overweights: list[ConstituentRow]
    top_underweights: list[ConstituentRow]
    provenance: Provenance


class RiskSummary(BaseModel):
    portfolio_id: str
    benchmark_id: str
    tracking_error_pct: float = Field(description="ex-ante, annualised, % ")
    portfolio_vol_pct: float
    benchmark_vol_pct: float
    beta: float
    realised_tracking_error_pct: float | None = None
    risk_coverage_pct: float = Field(description="share of sum|active weight| in the risk model")
    n_securities_in_model: int
    unit: str = "annualised %; beta is unitless"
    provenance: Provenance


class ContributionRow(BaseModel):
    group: str
    name: str | None = None
    active_weight_pct: float
    contribution_pct: float = Field(description="percentage points of annualised TE")
    pct_of_te: float = Field(description="share of TE, sums to 100 across all rows")
    unit: str = PP_TE


class RiskContributions(BaseModel):
    portfolio_id: str
    benchmark_id: str
    level: str
    tracking_error_pct: float
    rows: list[ContributionRow]
    n_total: int
    provenance: Provenance


class DurationRow(BaseModel):
    group: str
    portfolio_weight_pct: float
    benchmark_weight_pct: float
    contribution_to_active_duration_years: float


class DurationProfile(BaseModel):
    portfolio_id: str
    benchmark_id: str
    duration_years: float
    benchmark_duration_years: float
    active_duration_years: float
    nav: float
    nav_note: str = "notional NAV used for DV01; not the real fund size"
    dv01: float = Field(description="currency per 1bp parallel move, for `nav`")
    benchmark_dv01: float
    by_sector: list[DurationRow]
    unit: str = "duration in years; DV01 in USD per basis point"
    provenance: Provenance


class Metric(BaseModel):
    before: float
    after: float
    delta: float
    unit: str


class ContributionChange(BaseModel):
    security_id: str
    name: str | None
    contribution_before_pct: float
    contribution_after_pct: float
    delta_pct: float
    unit: str = PP_TE


class SimulationResult(BaseModel):
    portfolio_id: str
    benchmark_id: str
    hypothetical: bool = True
    note: str = "Simulation only. No order has been created."
    trades: list[dict[str, float | str]]
    funding: str
    metrics: dict[str, Metric]
    top_contribution_changes: list[ContributionChange]
    provenance: Provenance


class MethodologyResult(BaseModel):
    markdown: str
    provenance: Provenance

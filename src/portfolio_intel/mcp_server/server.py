"""MCP server: read-only portfolio analytics tools over stdio or streamable HTTP."""

import argparse
from functools import cache
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.types import ASGIApp

from portfolio_intel.analytics import models as m
from portfolio_intel.analytics.whatif import Trade
from portfolio_intel.service import PortfolioService

from .audit import audited
from .guardrails import MAX_TRADES, validate_portfolio, validate_top_n


@cache
def svc() -> PortfolioService:
    """Loaded on first use, so importing this module never touches the database."""
    return PortfolioService.from_settings()


mcp = MCPServer(
    "portfolio-intelligence",
    instructions=(
        "Read-only portfolio analytics. All numbers are computed deterministically by these "
        "tools. Quote tool outputs; do not recompute. No tool can place or modify orders. "
        "Omit benchmark_id to use the portfolio's own benchmark."
    ),
)

RO = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)

Dimension = Literal["sector", "industry", "industry_group", "country", "region", "currency"]
Level = Literal["security", "industry", "sector", "country"]


@mcp.tool(annotations=RO)
@audited
def list_portfolios() -> m.PortfolioList:
    """Portfolios and benchmarks available, with their benchmark, holding count and as-of date.
    PM portfolios: EQ_EU_PM (European equity), EQ_EU_VGK (Vanguard Europe), FI_US_PM (US bonds)."""
    return svc().list_portfolios()


@mcp.tool(annotations=RO)
@audited
def search_securities(query: str, portfolio_id: str | None = None) -> m.SecuritySearchResult:
    """Resolve a company or bond name/ticker (e.g. 'HSBC', 'Santander') to security_id values.
    With portfolio_id, only securities held by that portfolio or its benchmark are returned,
    with the current portfolio weight. If several match, ask the PM which one they mean."""
    if portfolio_id:
        validate_portfolio(svc(), portfolio_id)
    return svc().search_securities(query, portfolio_id)


@mcp.tool(annotations=RO)
@audited
def get_holdings(
    portfolio_id: str, top_n: int = 10, group_by: Dimension | None = None
) -> m.HoldingsResult:
    """Largest holdings by weight (percentage points of NAV), optionally with weights grouped
    by `group_by`. Bond holdings include duration (years) and maturity."""
    validate_portfolio(svc(), portfolio_id)
    return svc().get_holdings(portfolio_id, validate_top_n(top_n), group_by)


@mcp.tool(annotations=RO)
@audited
def get_active_exposures(
    portfolio_id: str,
    dimension: Dimension = "sector",
    filters: dict[str, str] | None = None,
    benchmark_id: str | None = None,
) -> m.ExposureResult:
    """Active weights (portfolio minus benchmark) grouped by `dimension`.
    Values are percentage points of NAV. Use filters to restrict the universe,
    e.g. dimension='industry', filters={'region': 'Europe'} for 'European banks'
    (then read the 'Banks' row). Also returns the largest over- and underweight securities."""
    validate_portfolio(svc(), portfolio_id)
    return svc().get_active_exposures(portfolio_id, dimension, filters, benchmark_id)


@mcp.tool(annotations=RO)
@audited
def get_risk_summary(portfolio_id: str, benchmark_id: str | None = None) -> m.RiskSummary:
    """Ex-ante tracking error, portfolio and benchmark volatility (annualised %), beta, and
    realised (ex-post) TE when both are real funds. Reports risk_coverage_pct: the share of
    active weight inside the risk model. Equity portfolios only; for bonds use
    get_duration_profile."""
    validate_portfolio(svc(), portfolio_id)
    return svc().get_risk_summary(portfolio_id, benchmark_id)


@mcp.tool(annotations=RO)
@audited
def get_risk_contributions(
    portfolio_id: str,
    level: Level = "security",
    top_n: int = 10,
    benchmark_id: str | None = None,
) -> m.RiskContributions:
    """Ex-ante tracking-error decomposition (Euler). Contributions are in percentage points
    of annualised TE and sum to total TE; pct_of_te sums to 100. Negative contributions
    mean the position diversifies the rest of the active book. Use for 'which positions
    drive my tracking error?'. Equity portfolios only."""
    validate_portfolio(svc(), portfolio_id)
    return svc().get_risk_contributions(portfolio_id, level, validate_top_n(top_n), benchmark_id)


@mcp.tool(annotations=RO)
@audited
def get_duration_profile(
    portfolio_id: str, benchmark_id: str | None = None, nav: float = 100e6
) -> m.DurationProfile:
    """Bond portfolio duration, benchmark duration and active duration (years), DV01 in USD
    per basis point for a notional `nav` (default $100mm), and contribution to active
    duration by sector. Bond portfolios only (e.g. FI_US_PM)."""
    validate_portfolio(svc(), portfolio_id)
    if not 0 < nav <= 1e12:
        raise ValueError("nav must be positive and at most 1e12")
    return svc().get_duration_profile(portfolio_id, benchmark_id, nav)


@mcp.tool(annotations=RO)
@audited
def simulate_trades(
    portfolio_id: str,
    trades: list[Trade],
    funding: Literal["cash", "pro_rata"] = "cash",
    benchmark_id: str | None = None,
) -> m.SimulationResult:
    """HYPOTHETICAL what-if. Applies weight changes in basis points of NAV to an in-memory
    copy of the portfolio and returns before/after/delta for TE, beta, exposures and duration.
    'Trim X by 50bps' means delta_weight_bps=-50 (0.50% of NAV, not half the position).
    Use security_id values from search_securities or get_holdings.
    Does not create or submit any order."""
    validate_portfolio(svc(), portfolio_id)
    if not trades:
        raise ValueError("Provide at least one trade.")
    if len(trades) > MAX_TRADES:
        raise ValueError(f"At most {MAX_TRADES} trades per simulation.")
    return svc().simulate_trades(portfolio_id, trades, funding, benchmark_id)


@mcp.tool(annotations=RO)
@audited
def get_methodology() -> m.MethodologyResult:
    """How every number is calculated: data sources, returns, covariance, tracking error and
    its decomposition, duration/DV01, what-if rules, and known limitations."""
    return svc().get_methodology()


@mcp.resource("methodology://risk")
def methodology() -> str:
    """Formulas, data sources and limitations."""
    return svc().methodology_markdown()


def http_app() -> ASGIApp:
    from starlette.routing import Route

    from .auth import build_http_app, healthz

    app = mcp.streamable_http_app(
        stateless_http=True,  # lets ECS run several tasks behind the ALB without sticky sessions
        json_response=True,
        host="0.0.0.0",
        # Bearer auth is the access control; DNS-rebinding protection would reject the ALB
        # and Docker host names, and browsers are not clients of this API.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    app.router.routes.append(Route("/healthz", healthz))
    return build_http_app(app)


def main() -> None:
    ap = argparse.ArgumentParser(prog="pi-mcp")
    ap.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    ap.add_argument("--port", type=int, default=8001)
    args = ap.parse_args()
    if args.transport == "stdio":
        mcp.run()
        return
    import uvicorn

    from portfolio_intel.telemetry import setup_tracing

    setup_tracing("pi-mcp")
    svc()  # load the snapshot before accepting traffic
    uvicorn.run(http_app(), host="0.0.0.0", port=args.port)

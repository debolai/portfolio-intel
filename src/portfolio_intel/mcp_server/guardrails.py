"""Input guardrails: entitlements per principal and hard bounds on tool arguments."""

from contextvars import ContextVar

from portfolio_intel.service import PortfolioService

MAX_TRADES = 20
MAX_TOP_N = 50

# Which portfolios each principal may analyse. Benchmarks are public index data and are
# readable by everyone. In production this would come from the OMS / identity provider.
PRINCIPAL_PORTFOLIOS: dict[str, set[str]] = {
    "pm-demo": {"EQ_EU_PM", "EQ_EU_VGK", "FI_US_PM"},
}

# Set by the HTTP auth middleware; stdio runs locally as the demo PM.
current_principal: ContextVar[str] = ContextVar("current_principal", default="pm-demo")


class EntitlementError(PermissionError): ...


def validate_portfolio(svc: PortfolioService, portfolio_id: str) -> None:
    if portfolio_id not in svc.portfolio.index:
        known = ", ".join(sorted(svc.portfolio.index))
        raise ValueError(f"Unknown portfolio {portfolio_id!r}. Known portfolios: {known}")
    if svc.portfolio.at[portfolio_id, "kind"] == "benchmark":
        return
    principal = current_principal.get()
    if portfolio_id not in PRINCIPAL_PORTFOLIOS.get(principal, set()):
        raise EntitlementError(f"{principal} is not entitled to portfolio {portfolio_id}.")


def validate_top_n(top_n: int) -> int:
    if top_n < 1:
        raise ValueError("top_n must be at least 1")
    return min(top_n, MAX_TOP_N)

"""FastAPI: /v1/ask (agent) + /v1/portfolios/* (analytics, no LLM)."""

import hmac
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Literal

from anthropic.types import MessageParam
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel

from portfolio_intel.agent.loop import ask
from portfolio_intel.analytics import models as m
from portfolio_intel.analytics.whatif import Trade, WhatIfError
from portfolio_intel.config import settings
from portfolio_intel.mcp_server.guardrails import MAX_TRADES, validate_portfolio, validate_top_n
from portfolio_intel.service import NotFoundError, PortfolioService
from portfolio_intel.telemetry import setup_tracing

CHAT_PAGE = Path(__file__).parent / "chat.html"
Dimension = Literal["sector", "industry", "industry_group", "country", "region", "currency"]
Level = Literal["security", "industry", "sector", "country"]


@cache
def svc() -> PortfolioService:
    return PortfolioService.from_settings()


def require_token(authorization: Annotated[str, Header()] = "") -> str:
    token = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(token, settings.mcp_token):
        raise HTTPException(status_code=401, detail="unauthorized")
    return "pm-demo"


Auth = Annotated[str, Depends(require_token)]

app = FastAPI(
    title="Portfolio Intelligence API",
    version="1.0.0",
    description="Read-only portfolio analytics. /v1/ask answers PM questions through an LLM "
    "agent that calls the MCP tools; every other endpoint is deterministic.",
)
FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz")


def _guard(pid: str) -> None:
    try:
        validate_portfolio(svc(), pid)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


def _run(fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except (ValueError, WhatIfError) as e:
        raise HTTPException(422, str(e)) from e


class AskIn(BaseModel):
    question: str
    history: list[MessageParam] | None = None


class SimulateIn(BaseModel):
    trades: list[Trade]
    funding: Literal["cash", "pro_rata"] = "cash"
    benchmark_id: str | None = None


@app.get("/healthz")
def health() -> dict[str, Any]:
    return {"ok": True, "snapshot": svc().snapshot_id}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def chat_page() -> str:
    return CHAT_PAGE.read_text()


@app.post("/v1/ask")
async def v1_ask(body: AskIn, _: Auth) -> dict[str, Any]:
    return await ask(body.question, body.history)


@app.get("/v1/portfolios")
def portfolios(_: Auth) -> m.PortfolioList:
    return svc().list_portfolios()


@app.get("/v1/portfolios/{pid}/holdings")
def holdings(
    pid: str, _: Auth, top_n: int = 10, group_by: Dimension | None = None
) -> m.HoldingsResult:
    _guard(pid)
    return _run(svc().get_holdings, pid, validate_top_n(top_n), group_by)  # type: ignore[no-any-return]


@app.get("/v1/portfolios/{pid}/exposures")
def exposures(
    pid: str,
    _: Auth,
    dimension: Dimension = "sector",
    region: str | None = None,
    benchmark_id: str | None = None,
) -> m.ExposureResult:
    _guard(pid)
    filters = {"region": region} if region else None
    return _run(svc().get_active_exposures, pid, dimension, filters, benchmark_id)  # type: ignore[no-any-return]


@app.get("/v1/portfolios/{pid}/risk")
def risk(pid: str, _: Auth, benchmark_id: str | None = None) -> m.RiskSummary:
    _guard(pid)
    return _run(svc().get_risk_summary, pid, benchmark_id)  # type: ignore[no-any-return]


@app.get("/v1/portfolios/{pid}/risk/contributions")
def contributions(
    pid: str, _: Auth, level: Level = "security", top_n: int = 10, benchmark_id: str | None = None
) -> m.RiskContributions:
    _guard(pid)
    return _run(  # type: ignore[no-any-return]
        svc().get_risk_contributions, pid, level, validate_top_n(top_n), benchmark_id
    )


@app.get("/v1/portfolios/{pid}/duration")
def duration(
    pid: str,
    _: Auth,
    benchmark_id: str | None = None,
    nav: Annotated[float, Query(gt=0, le=1e12)] = 100e6,
) -> m.DurationProfile:
    _guard(pid)
    return _run(svc().get_duration_profile, pid, benchmark_id, nav)  # type: ignore[no-any-return]


@app.post("/v1/portfolios/{pid}/simulate")
def simulate(pid: str, body: SimulateIn, _: Auth) -> m.SimulationResult:
    _guard(pid)
    if not 0 < len(body.trades) <= MAX_TRADES:
        raise HTTPException(422, f"Provide between 1 and {MAX_TRADES} trades.")
    return _run(  # type: ignore[no-any-return]
        svc().simulate_trades, pid, body.trades, body.funding, body.benchmark_id
    )


@app.get("/v1/methodology")
def methodology(_: Auth) -> m.MethodologyResult:
    return svc().get_methodology()


def main() -> None:
    import uvicorn

    setup_tracing("pi-agent-api")
    svc()  # load the snapshot before accepting traffic
    uvicorn.run(app, host="0.0.0.0", port=8000)

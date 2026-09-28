"""HTTP transport: bearer-token auth, /healthz, and W3C trace-context extraction."""

import hmac

from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from portfolio_intel.config import settings

from .guardrails import current_principal

PUBLIC_PATHS = {"/healthz"}


class BearerAuth:
    """Pure ASGI middleware (streams responses untouched, unlike BaseHTTPMiddleware)."""

    def __init__(self, app: ASGIApp, tokens: dict[str, str] | None = None):
        self.app = app
        # token -> principal; a static token is fine for a demo (OAuth in production)
        self.tokens = tokens if tokens is not None else {settings.mcp_token: "pm-demo"}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        header = Request(scope).headers.get("authorization", "")
        token = header.removeprefix("Bearer ").strip()
        principal = next(
            (p for t, p in self.tokens.items() if t and hmac.compare_digest(token, t)), None
        )
        if principal is None:
            await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
            return
        reset = current_principal.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            current_principal.reset(reset)


async def healthz(request: Request) -> Response:
    from .server import svc

    return JSONResponse({"ok": True, "snapshot": svc().snapshot_id})


def build_http_app(app: ASGIApp) -> ASGIApp:
    """Wrap the MCP Starlette app: auth inside, OpenTelemetry (traceparent) outside."""
    return OpenTelemetryMiddleware(BearerAuth(app), excluded_urls="healthz")

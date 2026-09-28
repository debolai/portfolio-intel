"""Append-only audit of every tool call, joined to traces by trace_id."""

import functools
import hashlib
import json
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from opentelemetry import trace
from pydantic import BaseModel

from portfolio_intel.config import settings

from .guardrails import current_principal
from .sinks import Sink, get_sink

tracer = trace.get_tracer("portfolio_intel.mcp")
_sink: Sink | None = None


def sink() -> Sink:
    global _sink
    if _sink is None:
        _sink = get_sink(settings)
    return _sink


def _h(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def result_hash(result: BaseModel) -> str:
    return _h(result.model_dump(mode="json"))


def audited[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    name = fn.__name__

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with tracer.start_as_current_span(f"execute_tool {name}") as span:
            span.set_attribute("gen_ai.operation.name", "execute_tool")
            span.set_attribute("gen_ai.tool.name", name)
            span.set_attribute("pi.args", json.dumps(kwargs, default=str)[:2000])
            t0, status, err = time.perf_counter(), "ok", None
            result: R | None = None
            try:
                result = fn(*args, **kwargs)
                return result
            except (ValueError, PermissionError, LookupError) as e:
                # Expected, user-facing errors: pass the message to the client so the agent can
                # recover (e.g. switch to pro_rata funding). Anything else stays masked.
                status, err = "error", str(e)
                span.record_exception(e)
                raise ToolError(str(e)) from e
            except Exception as e:
                status, err = "error", repr(e)
                span.record_exception(e)
                raise
            finally:
                payload = result.model_dump(mode="json") if isinstance(result, BaseModel) else None
                prov = (payload or {}).get("provenance", {})
                ctx = span.get_span_context()
                record = {
                    "event_id": str(uuid.uuid4()),
                    "ts": datetime.now(UTC).isoformat(),
                    "event": "tool_call",
                    "tool": name,
                    "status": status,
                    "error": err,
                    "principal": current_principal.get(),
                    "args": json.loads(json.dumps(kwargs, default=_dump)),
                    "args_sha256": _h(kwargs),
                    "result_sha256": _h(payload) if payload else None,
                    "calc_id": prov.get("calc_id"),
                    "data_snapshot_id": prov.get("data_snapshot_id"),
                    "methodology_version": prov.get("methodology_version"),
                    "trace_id": format(ctx.trace_id, "032x"),
                    "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
                }
                span.set_attribute("pi.calc_id", record["calc_id"] or "")
                sink().write(record)

    return wrapper


def _dump(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    return str(obj)

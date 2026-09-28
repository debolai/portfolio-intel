"""OpenTelemetry setup shared by all services: traces over OTLP/HTTP, JSON logs with trace ids."""

import logging
import os
from collections.abc import MutableMapping
from typing import Any

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from portfolio_intel.config import settings


def setup_tracing(service_name: str) -> None:
    """Install the global tracer provider. Exports only when OTEL_EXPORTER_OTLP_ENDPOINT is set,
    so tests and ad-hoc runs never block on a missing collector."""
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": os.environ.get("OTEL_SERVICE_NAME", service_name),
                "deployment.environment": settings.env,
                "pi.methodology_version": settings.methodology_version,
            }
        )
    )
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    setup_logging()


def _add_trace_ids(_: Any, __: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event["trace_id"] = format(ctx.trace_id, "032x")
        event["span_id"] = format(ctx.span_id, "016x")
    return event


def setup_logging(level: int = logging.INFO) -> None:
    """structlog JSON to stdout, with trace_id/span_id bound on every line."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _add_trace_ids,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
    )
    logging.basicConfig(level=level, format="%(message)s")

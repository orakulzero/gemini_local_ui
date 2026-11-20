"""
backend/app/observability/telemetry.py
Structured logging and OpenTelemetry tracing for Gemini Local UI.
"""

from __future__ import annotations

import contextvars
import logging
import os
import re
import uuid
from typing import Any, Callable, Dict, Optional

import structlog
from fastapi import Request, Response

_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(cookie|token|secret|password|authorization|psid|sapisid|snlm0e|dpapi|api_key)"
)

_trace_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
_configured_logging: bool = False
_tracer_provider: Any = None
_tracer: Any = None


def _scrub_value(val: Any) -> Any:
    if isinstance(val, dict):
        return {
            k: ("[REDACTED]" if _SENSITIVE_KEY_RE.search(str(k)) else _scrub_value(v))
            for k, v in val.items()
        }
    if isinstance(val, list):
        return [_scrub_value(item) for item in val]
    if isinstance(val, tuple):
        return tuple(_scrub_value(item) for item in val)
    return val


def redact_sensitive_processor(
    logger: Any, method_name: str, event_dict: Dict[str, Any]
) -> Dict[str, Any]:
    """Recursively redacts sensitive keys from log event dictionaries."""
    return _scrub_value(event_dict)


def configure_logging(json_logs: Optional[bool] = None) -> None:
    """Configures structlog idempotently."""
    global _configured_logging
    if _configured_logging:
        return

    use_json = json_logs if json_logs is not None else (
        os.environ.get("GEMINI_LOG_FORMAT", "").lower() == "json"
    )

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_sensitive_processor,
    ]

    if use_json:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=False))

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=False,
    )
    _configured_logging = True


def configure_telemetry(app: Any = None, *, span_exporter: Any = None) -> bool:
    """Initializes OpenTelemetry TracerProvider if enabled or span_exporter passed."""
    global _tracer_provider, _tracer
    enabled = (
        span_exporter is not None
        or os.environ.get("OTEL_ENABLED", "").lower() in ("1", "true", "yes")
    )
    if not enabled:
        return False

    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
            SimpleSpanProcessor,
        )

        provider = TracerProvider(resource=Resource.create({"service.name": "gemini-local-ui"}))
        if span_exporter is not None:
            provider.add_span_processor(SimpleSpanProcessor(span_exporter))
        elif os.environ.get("OTEL_EXPORTER", "").lower() == "console":
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

        _tracer_provider = provider
        _tracer = provider.get_tracer("gemini_local_ui")
        return True
    except Exception:
        return False


def reset_telemetry_for_tests() -> None:
    """Resets telemetry state between tests."""
    global _tracer_provider, _tracer
    if _tracer_provider is not None:
        try:
            _tracer_provider.shutdown()
        except Exception:
            pass
    _tracer_provider = None
    _tracer = None


class _NoopSpan:
    def __enter__(self) -> _NoopSpan:
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_attributes(self, attributes: Dict[str, Any]) -> None:
        pass

    def record_exception(self, exception: BaseException) -> None:
        pass

    def set_status(self, *args: Any, **kwargs: Any) -> None:
        pass


class _NoopTracer:
    def start_as_current_span(self, name: str, *args: Any, **kwargs: Any) -> _NoopSpan:
        return _NoopSpan()

    def start_span(self, name: str, *args: Any, **kwargs: Any) -> _NoopSpan:
        return _NoopSpan()


def get_tracer(name: str = "gemini_local_ui") -> Any:
    """Returns active OTel tracer or no-op tracer."""
    if _tracer_provider is not None:
        return _tracer_provider.get_tracer(name)
    return _NoopTracer()


def current_trace_id() -> str:
    """Returns 32-hex trace ID from bound contextvar, active span, or new uuid."""
    ctx_id = _trace_id_ctx.get()
    if ctx_id:
        return ctx_id
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        if span and span.is_recording():
            ctx = span.get_span_context()
            if ctx and ctx.trace_id:
                return f"{ctx.trace_id:032x}"
    except Exception:
        pass
    new_id = uuid.uuid4().hex
    _trace_id_ctx.set(new_id)
    return new_id


def bind_trace_context(trace_id: str, **fields: Any) -> None:
    """Binds trace ID and additional fields to contextvars."""
    _trace_id_ctx.set(trace_id)
    structlog.contextvars.bind_contextvars(trace_id=trace_id, **fields)


def clear_trace_context() -> None:
    """Clears trace contextvars."""
    _trace_id_ctx.set("")
    structlog.contextvars.clear_contextvars()


def get_logger(name: str = "gemini_local_ui") -> Any:
    """Returns a structlog bound logger."""
    return structlog.get_logger(name)


def _build_parent_span_context(trace_id_hex: str) -> Any:
    if _tracer_provider is None:
        return None
    try:
        from opentelemetry import trace
        from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

        span_ctx = SpanContext(
            trace_id=int(trace_id_hex, 16),
            span_id=int.from_bytes(os.urandom(8), "big") or 1,
            is_remote=True,
            trace_flags=TraceFlags(0x01),
        )
        return trace.set_span_in_context(NonRecordingSpan(span_ctx))
    except Exception:
        return None


async def trace_middleware(request: Request, call_next: Callable[[Request], Any]) -> Response:
    """HTTP middleware setting request state, binding structlog context, and creating OTel root span."""
    in_trace = request.headers.get("X-Trace-Id", "").strip()
    trace_id = in_trace if (in_trace and len(in_trace) == 32 and all(c in "0123456789abcdefABCDEF" for c in in_trace)) else uuid.uuid4().hex
    request.state.trace_id = trace_id
    bind_trace_context(trace_id, method=request.method, path=request.url.path)

    tracer = get_tracer("gemini_local_ui.http")
    span_name = f"{request.method} {request.url.path}"
    with tracer.start_as_current_span(span_name) as span:
        span.set_attribute("http.trace_id", trace_id)
        span.set_attribute("http.method", request.method)
        span.set_attribute("http.route", request.url.path)
        try:
            response = await call_next(request)
            span.set_attribute("http.status_code", response.status_code)
            response.headers["X-Trace-Id"] = trace_id
            return response
        finally:
            clear_trace_context()

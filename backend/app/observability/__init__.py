"""
backend/app/observability/__init__.py
Observability package re-exporting structured logging and OpenTelemetry helpers.
"""

from backend.app.observability.telemetry import (
    bind_trace_context,
    clear_trace_context,
    configure_logging,
    configure_telemetry,
    current_trace_id,
    get_logger,
    get_tracer,
    redact_sensitive_processor,
    reset_telemetry_for_tests,
    trace_middleware,
)

__all__ = [
    "bind_trace_context",
    "clear_trace_context",
    "configure_logging",
    "configure_telemetry",
    "current_trace_id",
    "get_logger",
    "get_tracer",
    "redact_sensitive_processor",
    "reset_telemetry_for_tests",
    "trace_middleware",
]

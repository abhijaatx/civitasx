"""Optional OpenTelemetry instrumentation with a safe no-dependency fallback."""

from __future__ import annotations

import os
from collections.abc import Callable
from functools import wraps
from inspect import iscoroutinefunction
from typing import Any, TypeVar, cast

try:
    from opentelemetry import trace

    _TRACER = trace.get_tracer("civitasx")
except ImportError:  # pragma: no cover - minimal install fallback
    _TRACER = None


def configure_telemetry(enabled: bool) -> None:
    """Install an SDK provider when telemetry is explicitly enabled."""

    global _TRACER
    if not enabled or _TRACER is None:
        return
    try:
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        if endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            exporter = OTLPSpanExporter(endpoint=endpoint)
        else:
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter

            exporter = ConsoleSpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        otel_trace.set_tracer_provider(provider)
        _TRACER = otel_trace.get_tracer("civitasx")
    except ImportError:
        # The API remains runnable with only opentelemetry-api installed.
        return


T = TypeVar("T")


def traced(name: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Trace synchronous functions without making telemetry a hard dependency."""

    def decorator(function: Callable[..., T]) -> Callable[..., T]:
        if iscoroutinefunction(function):
            async def async_wrapper(*args: Any, **kwargs: Any) -> T:
                if _TRACER is None:
                    return await function(*args, **kwargs)  # type: ignore[misc]
                with _TRACER.start_as_current_span(name) as span:
                    span.set_attribute("civitas.function", function.__qualname__)
                    try:
                        result = await function(*args, **kwargs)  # type: ignore[misc]
                        span.set_attribute("civitas.status", "ok")
                        return result
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_attribute("civitas.status", "error")
                        raise

            return cast(Callable[..., T], wraps(function)(async_wrapper))

        @wraps(function)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            if _TRACER is None:
                return function(*args, **kwargs)
            with _TRACER.start_as_current_span(name) as span:
                span.set_attribute("civitas.function", function.__qualname__)
                try:
                    result = function(*args, **kwargs)
                    span.set_attribute("civitas.status", "ok")
                    return result
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_attribute("civitas.status", "error")
                    raise

        return cast(Callable[..., T], wrapper)

    return decorator

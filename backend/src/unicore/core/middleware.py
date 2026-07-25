"""Request access logging (duration_ms per request) and the JSON error envelope."""

import time
import traceback
from collections.abc import Awaitable, Callable

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from opentelemetry import trace

from unicore.core.logging import get_logger


async def access_log_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    structlog.contextvars.clear_contextvars()
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = int((time.monotonic() - start) * 1000)
    get_logger().info(
        "http request completed",
        duration_ms=duration_ms,
        **{
            "http.request.method": request.method,
            "http.route": request.url.path,
            "http.response.status_code": response.status_code,
        },
    )
    return response


def install_error_envelope(app: FastAPI) -> None:
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        get_logger().error(
            "unhandled exception",
            **{
                "http.request.method": request.method,
                "http.route": request.url.path,
                "error.type": type(exc).__name__,
                "error.stack": traceback.format_exc(),
            },
        )
        ctx = trace.get_current_span().get_span_context()
        trace_id = format(ctx.trace_id, "032x") if ctx and ctx.is_valid else None
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred.",
                    "trace_id": trace_id,
                }
            },
        )

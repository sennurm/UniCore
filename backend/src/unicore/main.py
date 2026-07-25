from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

from unicore.core.config import get_settings
from unicore.core.health import router as health_router
from unicore.core.logging import configure_logging
from unicore.core.middleware import access_log_middleware, install_error_envelope


def _configure_tracing(service_name: str) -> None:
    # Valid span contexts are required so logs carry real trace/span ids
    # (the standard forbids all-zero ids). Exporters are wired per-environment later.
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        trace.set_tracer_provider(provider)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.service_name)
    _configure_tracing(settings.service_name)

    app = FastAPI(title="UniCore", version="0.1.0")
    app.middleware("http")(access_log_middleware)
    install_error_envelope(app)
    app.include_router(health_router)

    FastAPIInstrumentor.instrument_app(app)
    return app


app = create_app()

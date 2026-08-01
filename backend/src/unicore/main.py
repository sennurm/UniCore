from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

from unicore.core.config import get_settings
from unicore.core.health import router as health_router
from unicore.core.logging import configure_logging
from unicore.core.middleware import access_log_middleware, install_error_envelope
from unicore.core.security import (
    auth_gate_middleware,
    register_permission_checker,
    register_token_verifier,
)
from unicore.core.templates_router import router as templates_router
from unicore.modules.audit.router import router as audit_router
from unicore.modules.auth import service as auth_service
from unicore.modules.auth.router import router as auth_router
from unicore.modules.onboarding import service as onboarding_service
from unicore.modules.onboarding.router import router as onboarding_router
from unicore.modules.org import service as org_service
from unicore.modules.org.router import router as org_router
from unicore.modules.rbac import service as rbac_service
from unicore.modules.rbac.router import router as rbac_router
from unicore.modules.timetable.router import router as timetable_router
from unicore.modules.user.router import router as user_router

MODULE_ROUTERS = (
    auth_router,
    user_router,
    org_router,
    rbac_router,
    audit_router,
    timetable_router,
    onboarding_router,
    templates_router,
)


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
    # Middleware order: later registrations wrap earlier ones, so the access log
    # is outermost and records auth-gate rejections too.
    app.middleware("http")(auth_gate_middleware)
    app.middleware("http")(access_log_middleware)
    install_error_envelope(app)
    app.include_router(health_router)
    for module_router in MODULE_ROUTERS:
        app.include_router(module_router)

    app.add_middleware(  # outermost: preflight handled before the auth gate
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )

    FastAPIInstrumentor.instrument_app(app)

    # Real session verification + outbox subscriptions (both idempotent).
    register_token_verifier(auth_service.verify_session_token)
    register_permission_checker(rbac_service.check_permission)
    org_service.register_position_reader(onboarding_service.positions_in_programme)
    rbac_service.register_event_handlers()
    return app


app = create_app()

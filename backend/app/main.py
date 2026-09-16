"""Azienda API application factory.

- Auto-discovers routers: every module in ``app.api.routers`` exposing
  ``router`` is mounted under ``/api/v1``. Builders add a file; no registry edit.
- Middleware: request-id (+ structlog binding), error envelope (API.md §1).
- Lifespan: settings validation, engine init, logging config.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import app.api.routers as routers_pkg
from app.core.config import Settings, settings as global_settings
from app.core.db import get_engine, init_engine
from app.core.errors import install_error_handlers
from app.core.logging import (bind_request, clear_request_context,
                              configure_logging, get_logger, new_request_id)

log = get_logger(__name__)


def _discover_routers(app: FastAPI, prefix: str = "/api/v1") -> list[str]:
    mounted: list[str] = []
    for info in pkgutil.iter_modules(routers_pkg.__path__):
        module = importlib.import_module(f"{routers_pkg.__name__}.{info.name}")
        router = getattr(module, "router", None)
        if router is None:
            continue
        app.include_router(router, prefix=prefix)
        mounted.append(info.name)
    return mounted


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    configure_logging(settings)
    if settings.is_production:
        settings.require_jwt_secret()  # fail fast
    init_engine(settings)
    # Warm the engine so a bad DATABASE_URL fails at startup, not first request.
    engine = get_engine()
    async with engine.connect():
        pass
    log.info("azienda api starting", version=settings.version,
             environment=settings.environment)
    yield
    await engine.dispose()
    log.info("azienda api stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app. ``settings`` override exists for tests."""
    app_settings = settings or global_settings
    app = FastAPI(
        title="Azienda",
        version=app_settings.version,
        description="The AI Business OS — business applications, run by agents, "
                    "governed by policy.",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.state.settings = app_settings
    install_error_handlers(app)

    @app.middleware("http")
    async def _request_id_middleware(request: Request, call_next):  # noqa: ANN001, ANN202
        request_id = request.headers.get("X-Request-ID") or new_request_id()
        request.state.request_id = request_id
        bind_request(request_id)
        try:
            response = await call_next(request)
        finally:
            clear_request_context()
        response.headers["X-Request-ID"] = request_id
        return response

    mounted = _discover_routers(app)

    @app.get("/healthz", tags=["platform"])
    async def healthz():  # noqa: ANN202
        return {"status": "ok", "version": app_settings.version,
                "routers": sorted(mounted)}

    @app.get("/", tags=["platform"])
    async def root():  # noqa: ANN202
        return {"name": "Azienda", "version": app_settings.version,
                "docs": "/docs", "api": "/api/v1"}

    # Unhandled-path JSON (instead of Starlette's default HTML via handler above).
    @app.exception_handler(404)
    async def _not_found(request: Request, exc):  # noqa: ANN001, ANN202
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "not_found", "message": "not found",
                               "details": {"path": request.url.path},
                               "trace_id": getattr(request.state, "request_id",
                                                   "unknown")}})

    logging.getLogger(__name__).info("routers mounted", routers=sorted(mounted))
    return app


app = create_app()

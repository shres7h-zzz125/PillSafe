"""FastAPI application entrypoint for PillSafe."""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import Settings, get_settings
from app.dependencies import build_container
from app.routers.health import router as health_router
from app.routers.medicines import router as medicines_router


def create_app(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    cfg = settings or get_settings()
    app = FastAPI(
        title=cfg.app_name,
        version=cfg.app_version,
        description=(
            "PillSafe backend for OCR-compatible medicine search, active ingredient resolution, "
            "user confirmation, and safety-first interaction/duplication checks."
        ),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.container = build_container(cfg, transport=transport)
    app.include_router(health_router)
    app.include_router(medicines_router)
    return app


app = create_app()

"""FastAPI application entrypoint."""

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin_router import router as admin_router
from app.api.public_router import router as public_router
from app.api.whatsapp_webhook import router as whatsapp_webhook_router
from app.core.config import settings
from app.services import whatsapp_calling_client
from app.services.llm_client import close_llm_http_client


logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Own shared network clients and calling resources for the app lifetime."""
    await whatsapp_calling_client.startup()
    try:
        yield
    finally:
        await whatsapp_calling_client.shutdown()
        await close_llm_http_client()


app = FastAPI(title="Pune Metro AI WhatsApp Assistant", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.ADMIN_DASHBOARD_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(whatsapp_webhook_router)
app.include_router(admin_router)
app.include_router(public_router)


@app.get("/health")
async def health_check() -> dict[str, str | bool]:
    """Return service health for container orchestration."""
    return {"status": "ok", "calling_enabled": settings.WHATSAPP_CALLING_ENABLED, "calling_ready": whatsapp_calling_client.is_ready()}

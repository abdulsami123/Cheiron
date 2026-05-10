from __future__ import annotations

import ssl
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from openai import AsyncOpenAI

from .config import get_settings
from .errors import install_error_handlers
from .logging_config import configure_logging, get_logger
from .routers import trials


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger(__name__)

    timeout = httpx.Timeout(
        connect=settings.ctgov_connect_timeout_s,
        read=settings.ctgov_read_timeout_s,
        write=settings.ctgov_write_timeout_s,
        pool=settings.ctgov_pool_timeout_s,
    )
    # ClinicalTrials.gov's edge rejects httpx's stricter default cipher set
    # with a 403; using OpenSSL's DEFAULT cipher list (what urllib uses) is
    # accepted. This keeps us on httpx.AsyncClient end-to-end.
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.set_ciphers("DEFAULT:@SECLEVEL=2")

    http_client = httpx.AsyncClient(
        timeout=timeout,
        headers={"User-Agent": "cheiron2/0.1 (+httpx)"},
        follow_redirects=True,
        verify=ssl_ctx,
    )

    # Construct AsyncOpenAI even without a real key so the service can start in
    # dev / health-check contexts. Calls without a real key will fail upstream
    # and surface as a 502 LLMPlanningError.
    openai_client = AsyncOpenAI(
        api_key=settings.openai_api_key or "missing-openai-api-key",
        base_url=settings.openai_base_url,
        timeout=settings.openai_timeout_s,
    )

    app.state.http_client = http_client
    app.state.openai_client = openai_client
    log.info(
        "lifespan startup: ctgov=%s openai_base=%s model=%s",
        settings.ctgov_base_url,
        settings.openai_base_url,
        settings.openai_model,
    )

    try:
        yield
    finally:
        log.info("lifespan shutdown: closing clients")
        await http_client.aclose()
        await openai_client.close()


app = FastAPI(
    title="Cheiron2 — ClinicalTrials.gov visualizer",
    version="0.1.0",
    lifespan=lifespan,
)

install_error_handlers(app)
app.include_router(trials.router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}

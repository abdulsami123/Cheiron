from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import Depends, Request
from openai import AsyncOpenAI

from .config import Settings, get_settings


def get_http_client(request: Request) -> httpx.AsyncClient:
    client: httpx.AsyncClient | None = getattr(request.app.state, "http_client", None)
    if client is None:
        raise RuntimeError("httpx client is not initialized; check the lifespan.")
    return client


def get_openai_client(request: Request) -> AsyncOpenAI:
    client: AsyncOpenAI | None = getattr(request.app.state, "openai_client", None)
    if client is None:
        raise RuntimeError("OpenAI client is not initialized; check the lifespan.")
    return client


def get_settings_dep() -> Settings:
    return get_settings()


HttpClientDep = Annotated[httpx.AsyncClient, Depends(get_http_client)]
OpenAIClientDep = Annotated[AsyncOpenAI, Depends(get_openai_client)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]

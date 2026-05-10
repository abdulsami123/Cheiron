from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    ctgov_base_url: str = Field(
        default="https://clinicaltrials.gov/api/v2",
        description="ClinicalTrials.gov v2 API base URL.",
    )
    ctgov_page_size: int = Field(default=50, ge=1, le=1000)
    ctgov_connect_timeout_s: float = 5.0
    ctgov_read_timeout_s: float = 20.0
    ctgov_write_timeout_s: float = 5.0
    ctgov_pool_timeout_s: float = 5.0

    openai_api_key: str = Field(default="", description="OpenAI / compatible API key.")
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        description="OpenAI-compatible base URL (Azure / vLLM / OpenRouter).",
    )
    openai_model: str = Field(default="gpt-4.1")
    openai_timeout_s: float = 60.0

    llm_max_studies: int = Field(default=50, ge=1, le=500)
    llm_max_chars: int = Field(default=120_000, ge=1000)

    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

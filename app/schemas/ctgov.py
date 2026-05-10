"""Minimal Pydantic models for the ClinicalTrials.gov v2 fields we actually use.

We intentionally keep these permissive (extra='allow') because the upstream payload
is large and we only consume a stable subset of fields. The trimming layer reads
from the raw dict to stay resilient to schema drift; these models exist mainly to
document the shape and to type-check the small surface we do touch.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CtgovStudy(BaseModel):
    model_config = ConfigDict(extra="allow")
    protocolSection: dict[str, Any] | None = None
    derivedSection: dict[str, Any] | None = None
    hasResults: bool | None = None


class CtgovStudiesResponse(BaseModel):
    """Response from GET /studies (search)."""

    model_config = ConfigDict(extra="allow")
    studies: list[dict[str, Any]] = Field(default_factory=list)
    nextPageToken: str | None = None
    totalCount: int | None = None


class CtgovSingleStudyResponse(BaseModel):
    """Response from GET /studies/{nctId}.

    The v2 API returns the study object directly (not wrapped in `studies`).
    """

    model_config = ConfigDict(extra="allow")
    protocolSection: dict[str, Any] | None = None
    derivedSection: dict[str, Any] | None = None
    hasResults: bool | None = None

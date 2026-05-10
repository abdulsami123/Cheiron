from __future__ import annotations

import re
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, ConfigDict, field_validator

NCT_RE = re.compile(r"^NCT\d{8}$")


class TrialsQuery(BaseModel):
    """Validated query-string inputs for GET /trials/visualize."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str
    conditions: str | None = None
    location: str | None = None
    title: str | None = None
    intervention: str | None = None
    outcome_measure: str | None = None
    sponsor: str | None = None
    lead: str | None = None
    study_id: str | None = None
    nct_id: str | None = None

    @field_validator("query")
    @classmethod
    def _query_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("query must be a non-empty string")
        return v

    @field_validator("nct_id")
    @classmethod
    def _validate_nct_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not NCT_RE.match(v):
            raise ValueError("nct_id must match ^NCT\\d{8}$ (e.g. NCT01234567)")
        return v

    def non_null_filters(self) -> dict[str, str]:
        return {
            k: v
            for k, v in self.model_dump().items()
            if k not in {"query"} and v is not None
        }


# Reusable Annotated[Query] aliases so the route signature stays compact.
QueryParam = Annotated[str, Query(min_length=1, description="Free-text user question.")]
OptionalStr = Annotated[str | None, Query(default=None)]
OptionalNct = Annotated[
    str | None,
    Query(
        default=None,
        pattern=r"^NCT\d{8}$",
        description="NCT identifier, e.g. NCT01234567.",
    ),
]

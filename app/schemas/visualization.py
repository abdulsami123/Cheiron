from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class VisualizationType(str, Enum):
    BAR_CHART = "bar_chart"
    SCATTER_PLOT = "scatter_plot"
    TIME_SERIES = "time_series"
    HISTOGRAM = "histogram"
    NETWORK_GRAPH = "network_graph"


# ---------- LLM-facing (Structured Outputs compatible) ----------
# OpenAI strict mode rejects free-form maps (dict[str, X]) and any object
# without an explicit closed `properties` set. So the LLM-facing model uses
# closed records throughout, with the server mapping back to the friendlier
# dict-shaped public response.


class EncodingLLM(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: str | None
    y: str | None
    color: str | None
    size: str | None
    group: str | None
    tooltip: list[str]


class VisualizationLLM(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: VisualizationType
    title: str
    encoding: EncodingLLM
    # Per-row shape varies by viz type; OpenAI strict mode forbids free-form
    # objects, so the LLM emits a JSON string and the server parses it.
    data_json: str


class UnitEntryLLM(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    unit: str


class SortSpecLLM(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    direction: Literal["asc", "desc"]


class MetadataLLM(BaseModel):
    model_config = ConfigDict(extra="forbid")
    units: list[UnitEntryLLM]
    sort: SortSpecLLM | None
    notes: str | None


class TrialsVisualizationLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visualization: VisualizationLLM
    metadata: MetadataLLM


# ---------- Public response (server augments metadata) ----------


class Encoding(BaseModel):
    x: str | None = None
    y: str | None = None
    color: str | None = None
    size: str | None = None
    group: str | None = None
    tooltip: list[str] = Field(default_factory=list)


class Visualization(BaseModel):
    type: VisualizationType
    title: str
    encoding: Encoding
    data: list[dict[str, Any]]


class Metadata(BaseModel):
    units: dict[str, str] = Field(default_factory=dict)
    sort: dict[str, Literal["asc", "desc"]] | None = None
    total_studies: int | None = None
    nct_ids: list[str] = Field(default_factory=list)
    notes: str | None = None
    source: Literal["clinicaltrials.gov"] = "clinicaltrials.gov"
    generated_at: datetime


class TrialsVisualizationResponse(BaseModel):
    visualization: Visualization
    metadata: Metadata


# ---------- Mapping ----------


def _parse_data_json(data_json: str) -> list[dict[str, Any]]:
    """Parse the LLM's JSON-encoded data array into row dicts."""
    try:
        decoded = json.loads(data_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(decoded, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in decoded:
        if isinstance(item, dict):
            rows.append(item)
    return rows


def map_llm_to_public(
    llm: TrialsVisualizationLLMOutput,
    *,
    total_studies: int | None,
    nct_ids: list[str],
    extra_notes: str | None = None,
) -> TrialsVisualizationResponse:
    """Map the LLM's strict output to the public response, injecting server metadata."""
    enc_in = llm.visualization.encoding
    encoding = Encoding(
        x=enc_in.x,
        y=enc_in.y,
        color=enc_in.color,
        size=enc_in.size,
        group=enc_in.group,
        tooltip=list(enc_in.tooltip),
    )
    visualization = Visualization(
        type=llm.visualization.type,
        title=llm.visualization.title,
        encoding=encoding,
        data=_parse_data_json(llm.visualization.data_json),
    )

    units = {entry.field: entry.unit for entry in llm.metadata.units}
    sort = (
        {llm.metadata.sort.field: llm.metadata.sort.direction}
        if llm.metadata.sort is not None
        else None
    )

    notes = llm.metadata.notes
    if extra_notes:
        notes = f"{notes}\n{extra_notes}" if notes else extra_notes

    metadata = Metadata(
        units=units,
        sort=sort,
        total_studies=total_studies,
        nct_ids=list(nct_ids),
        notes=notes,
        generated_at=datetime.now(timezone.utc),
    )
    return TrialsVisualizationResponse(visualization=visualization, metadata=metadata)

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from ..deps import HttpClientDep, OpenAIClientDep, SettingsDep
from ..errors import LLMPlanningError
from ..logging_config import get_logger
from ..schemas.request import TrialsQuery
from ..schemas.visualization import TrialsVisualizationResponse, map_llm_to_public
from ..services.clinicaltrials import fetch_single_study, search_studies
from ..services.llm import (
    ESSIE_PROMPT_VERSION,
    PROMPT_VERSION,
    plan_visualization,
    translate_query_to_essie,
)
from ..services.trimming import extract_nct_ids, trim_studies, trim_study_payload

log = get_logger(__name__)

router = APIRouter(prefix="/trials", tags=["trials"])


@router.get("/visualize", response_model=TrialsVisualizationResponse)
async def visualize_trials(
    http_client: HttpClientDep,
    openai_client: OpenAIClientDep,
    settings: SettingsDep,
    query: Annotated[str, Query(min_length=1, description="Free-text user question.")],
    conditions: Annotated[str | None, Query(description="Disease/condition filter.")] = None,
    location: Annotated[str | None, Query(description="Geographic filter.")] = None,
    title: Annotated[str | None, Query(description="Title keyword.")] = None,
    intervention: Annotated[str | None, Query(description="Intervention name.")] = None,
    outcome_measure: Annotated[str | None, Query(description="Outcome measure keyword.")] = None,
    sponsor: Annotated[str | None, Query(description="Sponsor name.")] = None,
    lead: Annotated[str | None, Query(description="Lead investigator/org.")] = None,
    study_id: Annotated[str | None, Query(description="Org study ID.")] = None,
    nct_id: Annotated[
        str | None,
        Query(pattern=r"^NCT\d{8}$", description="NCT identifier, e.g. NCT01234567."),
    ] = None,
) -> TrialsVisualizationResponse:
    """Fetch trial data and return an LLM-chosen visualization spec.

    Pipeline: free-text `query` → LLM Essie translator → ClinicalTrials.gov
    search → LLM visualization planner. When `nct_id` is provided, the search
    + Essie translation are skipped and a single-study lookup runs instead.
    """
    parsed = TrialsQuery(
        query=query,
        conditions=conditions,
        location=location,
        title=title,
        intervention=intervention,
        outcome_measure=outcome_measure,
        sponsor=sponsor,
        lead=lead,
        study_id=study_id,
        nct_id=nct_id,
    )

    nct_ids: list[str]
    total_studies: int | None
    trial_data: list[dict[str, Any]] | dict[str, Any]
    essie_note: str | None = None

    if parsed.nct_id:
        single = await fetch_single_study(http_client, settings, parsed.nct_id)
        trimmed = trim_study_payload(single)
        trial_data = [trimmed]
        nct_ids = [trimmed["nct_id"]] if trimmed.get("nct_id") else [parsed.nct_id]
        total_studies = 1
    else:
        # LLM 1: natural-language query → Essie expression for `query.term`.
        # Without translation the upstream ANDs every English word and matches nothing.
        search_term = parsed.query
        try:
            translation = await translate_query_to_essie(
                openai_client, settings, user_query=parsed.query
            )
            search_term = translation.expression or parsed.query
            essie_note = (
                f"essie_translation: {translation.expression!r} "
                f"(rationale: {translation.rationale})"
            )
            log.info(
                "essie translation: original=%r -> expression=%r",
                parsed.query,
                translation.expression,
            )
        except LLMPlanningError as e:
            log.warning("essie translation failed, using raw query: %s", e.message)
            essie_note = f"essie_translation_failed: {e.message}; used raw query."

        raw = await search_studies(
            http_client,
            settings,
            free_text=search_term,
            filters=parsed.non_null_filters(),
        )
        studies = raw.get("studies") or []
        nct_ids = extract_nct_ids(studies)
        total_studies = (
            raw.get("totalCount") if isinstance(raw.get("totalCount"), int) else len(studies)
        )
        trial_data = trim_studies(
            studies,
            max_studies=settings.llm_max_studies,
            max_chars=settings.llm_max_chars,
        )

    # LLM 2: visualization planner.
    llm_output = await plan_visualization(
        openai_client,
        settings,
        user_query=parsed.query,
        filters=parsed.non_null_filters(),
        trial_data=trial_data,
    )

    notes = f"prompt_version={PROMPT_VERSION}"
    if essie_note is not None:
        notes = f"{notes}; essie_prompt_version={ESSIE_PROMPT_VERSION}; {essie_note}"

    return map_llm_to_public(
        llm_output,
        total_studies=total_studies,
        nct_ids=nct_ids,
        extra_notes=notes,
    )

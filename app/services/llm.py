from __future__ import annotations

import json
from typing import TypeVar

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from ..config import Settings
from ..errors import LLMPlanningError
from ..logging_config import get_logger
from ..schemas.essie import EssieTranslation
from ..schemas.visualization import TrialsVisualizationLLMOutput

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

PROMPT_VERSION = "viz-planner.v1"
ESSIE_PROMPT_VERSION = "essie-translator.v1"

ESSIE_TRANSLATOR_SYSTEM_PROMPT = (
    "You translate a user's natural-language clinical-trial question into a "
    "ClinicalTrials.gov v2 Essie expression suitable for the `query.term` "
    "parameter. The user's free-text question is NOT a valid Essie expression "
    "by itself; without translation the search will AND every English word and "
    "return zero matches.\n\n"
    "Essie syntax recap:\n"
    "- Whitespace between terms means AND. Keep terms short and content-bearing.\n"
    "- Use OR for synonyms/alternatives, parenthesized: (cancer OR tumor OR neoplasm).\n"
    "- Use double-quoted phrases for multi-word terms: \"breast cancer\".\n"
    "- Use NOT for exclusions: NOT placebo.\n"
    "- DO NOT use trailing `*` wildcards in `query.term` — they're unreliable "
    "in v2 and frequently return zero. Use an OR-group of full words instead: "
    "(chemotherapy OR chemotherapeutic) NOT chemother*.\n"
    "- Combine: (\"breast cancer\" OR mammary) (chemotherapy OR chemotherapeutic) NOT placebo\n\n"
    "Translation rules:\n"
    "1. Drop interrogatives and stop-words: how, does, what, the, of, in, by, "
    "across, etc. Keep only content-bearing nouns/adjectives/verbs.\n"
    "2. CRITICAL: drop dimension/aspect words — words describing what the user "
    "wants to MEASURE or VISUALIZE rather than what to FILTER on. Examples: "
    "'enrollment', 'phase', 'count', 'rate', 'distribution', 'over time', "
    "'by year', 'by status', 'trend', 'breakdown'. These describe the question, "
    "not the trial set, and including them as AND'd terms returns zero.\n"
    "3. Keep ONLY filter-shaped concepts: diseases, drugs, populations, "
    "interventions, locations, sponsors. \"How does enrollment vary by phase "
    "in pediatric leukemia trials?\" → (pediatric OR paediatric OR child OR "
    "children) leukemia. NOT 'enrollment phase'.\n"
    "4. Identify domain synonyms and OR them. e.g. for 'pediatric' include "
    "(pediatric OR paediatric OR child OR children).\n"
    "5. For variant forms, use OR-groups of full words rather than wildcards: "
    "(chemotherapy OR chemotherapeutic), (immunotherapy OR immunotherapies).\n"
    "6. Keep multi-word medical terms as quoted phrases.\n"
    "7. If the input is just an NCT identifier (matching ^NCT\\d{8}$), produce "
    "the bare ID as the expression — the route handles single-study lookups "
    "separately and won't run a search.\n"
    "8. Produce a tight expression — too narrow returns zero, too broad returns noise. "
    "Aim for 2–4 AND-joined groups. Err toward broader (more matches) when in doubt.\n"
    "9. Populate `must_include` with the AND-joined groups (a single quoted phrase or "
    "stem each), `should_include` as OR-groups (each inner list is one OR-group), "
    "`must_exclude` with any NOT terms, and `expression` with the fully assembled "
    "Essie string that you would send.\n"
    "10. `rationale` is a one-line explanation of what you kept, dropped, and why."
)

PLANNER_SYSTEM_PROMPT = (
    "You are a clinical-trial data visualization planner. You receive a user's "
    "free-text question and a JSON payload of clinical-trial records from "
    "ClinicalTrials.gov. Choose exactly ONE visualization type from the allowed "
    "enum that best answers the user's question given the data, then populate "
    "every field of the response schema.\n\n"
    "Rules:\n"
    "1. `visualization.type` MUST be one of: bar_chart, scatter_plot, "
    "time_series, histogram, network_graph.\n"
    "2. `visualization.encoding` describes which row field maps to which channel. "
    "Set channels not used by the chosen viz to null. `tooltip` is always a list "
    "(possibly empty).\n"
    "3. `visualization.data_json` is a JSON-encoded STRING containing a list of "
    "row objects (e.g. '[{\"phase\":\"PHASE3\",\"count\":42}, ...]'). Keep row "
    "keys consistent with the encoding fields. Do NOT emit a JSON array — emit a "
    "string whose contents parse as a JSON array.\n"
    "4. Prefer concise, decision-useful aggregations over dumping raw rows: "
    "e.g. counts by phase, enrollment over start year, status distribution.\n"
    "4a. For `network_graph`, every row MUST be an edge with `source` and "
    "`target` string fields (the two endpoints). An optional `weight` numeric "
    "field is allowed. Set `encoding.x=\"source\"`, `encoding.y=\"target\"`. "
    "Do not invent other column names.\n"
    "5. `metadata.units` is a list of {field, unit} entries — one per numeric "
    "field that has a unit. Use [] if there are no units.\n"
    "6. `metadata.sort` is either null OR an object {field, direction} with "
    "direction in {'asc','desc'}.\n"
    "7. `metadata.notes` is a one-paragraph plain-language explanation of why "
    "this viz answers the user's question, or null.\n"
    "8. Return JSON only, matching the provided schema exactly. No commentary."
)


def _user_payload(
    user_query: str,
    filters: dict[str, str],
    trial_data: list[dict] | dict,
) -> str:
    payload = {
        "user_query": user_query,
        "filters": filters,
        "trial_data": trial_data,
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


async def call_llm_structured(
    client: AsyncOpenAI,
    settings: Settings,
    *,
    system: str,
    user: str,
    schema: type[T],
) -> T:
    """Primary path: OpenAI Structured Outputs via beta.chat.completions.parse."""
    try:
        completion = await client.beta.chat.completions.parse(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format=schema,
        )
    except openai.APIError as e:
        log.warning("llm.structured api_error: %s", e)
        raise LLMPlanningError(
            "LLM API error during structured planning.",
            details={"error": str(e)},
        ) from e

    if not completion.choices:
        raise LLMPlanningError("LLM returned no choices.")

    choice = completion.choices[0]
    msg = choice.message

    refusal = getattr(msg, "refusal", None)
    if refusal:
        raise LLMPlanningError(
            "LLM refused the request.",
            details={"refusal": str(refusal)},
        )

    if choice.finish_reason and choice.finish_reason != "stop":
        raise LLMPlanningError(
            f"LLM stopped with finish_reason={choice.finish_reason}.",
            details={"finish_reason": choice.finish_reason},
        )

    parsed = msg.parsed
    if parsed is None:
        raise LLMPlanningError("LLM returned no parsed structured output.")
    return parsed  # type: ignore[return-value]


async def call_llm_text(
    client: AsyncOpenAI,
    settings: Settings,
    *,
    system: str,
    user: str,
) -> str:
    """Escape hatch: free-text completion. Used only for the repair fallback."""
    try:
        completion = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
        )
    except openai.APIError as e:
        raise LLMPlanningError(
            "LLM API error during text fallback.", details={"error": str(e)}
        ) from e
    if not completion.choices:
        raise LLMPlanningError("LLM (text) returned no choices.")
    content = completion.choices[0].message.content or ""
    return content


async def translate_query_to_essie(
    client: AsyncOpenAI,
    settings: Settings,
    *,
    user_query: str,
) -> EssieTranslation:
    """Translate a natural-language query to a ClinicalTrials.gov Essie expression.

    Uses Structured Outputs as the primary path; falls back to a single-shot
    JSON repair if the structured call fails.
    """
    user_msg = json.dumps({"user_query": user_query}, ensure_ascii=False)

    try:
        return await call_llm_structured(
            client,
            settings,
            system=ESSIE_TRANSLATOR_SYSTEM_PROMPT,
            user=user_msg,
            schema=EssieTranslation,
        )
    except LLMPlanningError as primary_err:
        log.warning(
            "llm.translate_to_essie primary path failed, attempting repair: %s",
            primary_err.message,
        )

    repair_system = (
        ESSIE_TRANSLATOR_SYSTEM_PROMPT
        + "\n\nReturn ONLY a JSON object matching this schema:\n"
        + json.dumps(EssieTranslation.model_json_schema(), ensure_ascii=False)
    )
    text = await call_llm_text(client, settings, system=repair_system, user=user_msg)
    try:
        return EssieTranslation.model_validate_json(text)
    except ValidationError as e:
        raise LLMPlanningError(
            "Essie translation repair output failed schema validation.",
            details={"validation_errors": json.loads(e.json())},
        ) from e


async def plan_visualization(
    client: AsyncOpenAI,
    settings: Settings,
    *,
    user_query: str,
    filters: dict[str, str],
    trial_data: list[dict] | dict,
) -> TrialsVisualizationLLMOutput:
    """High-level planner: structured-outputs primary path + single-shot repair."""
    user_msg = _user_payload(user_query, filters, trial_data)

    try:
        return await call_llm_structured(
            client,
            settings,
            system=PLANNER_SYSTEM_PROMPT,
            user=user_msg,
            schema=TrialsVisualizationLLMOutput,
        )
    except LLMPlanningError as primary_err:
        log.warning("llm.plan primary path failed, attempting single-shot repair: %s", primary_err.message)

    repair_system = (
        PLANNER_SYSTEM_PROMPT
        + "\n\nReturn ONLY a JSON object matching this schema:\n"
        + json.dumps(TrialsVisualizationLLMOutput.model_json_schema(), ensure_ascii=False)
    )
    try:
        text = await call_llm_text(client, settings, system=repair_system, user=user_msg)
    except LLMPlanningError as e:
        raise e

    try:
        return TrialsVisualizationLLMOutput.model_validate_json(text)
    except ValidationError as e:
        raise LLMPlanningError(
            "LLM repair output failed schema validation.",
            details={"validation_errors": json.loads(e.json())},
        ) from e

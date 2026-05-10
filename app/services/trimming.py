from __future__ import annotations

import json
from typing import Any

from ..logging_config import get_logger

log = get_logger(__name__)


def _path(d: Any, *keys: str) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def trim_study_payload(study: dict[str, Any]) -> dict[str, Any]:
    """Reduce a single ClinicalTrials.gov v2 study to a stable LLM-friendly subset.

    Fields kept: NCT ID, brief title, overall status, phase, conditions,
    interventions, locations, sponsor, start/completion dates, enrollment,
    primary outcomes.
    """
    proto = study.get("protocolSection") or {}

    ident = proto.get("identificationModule") or {}
    status_mod = proto.get("statusModule") or {}
    sponsor_mod = proto.get("sponsorCollaboratorsModule") or {}
    design_mod = proto.get("designModule") or {}
    cond_mod = proto.get("conditionsModule") or {}
    arms_mod = proto.get("armsInterventionsModule") or {}
    contacts_mod = proto.get("contactsLocationsModule") or {}
    outcomes_mod = proto.get("outcomesModule") or {}

    interventions = [
        {
            "name": iv.get("name"),
            "type": iv.get("type"),
        }
        for iv in (arms_mod.get("interventions") or [])
    ]

    locations = [
        {
            "facility": loc.get("facility"),
            "city": loc.get("city"),
            "state": loc.get("state"),
            "country": loc.get("country"),
        }
        for loc in (contacts_mod.get("locations") or [])
    ]

    primary_outcomes = [
        {
            "measure": o.get("measure"),
            "timeFrame": o.get("timeFrame"),
        }
        for o in (outcomes_mod.get("primaryOutcomes") or [])
    ]

    return {
        "nct_id": ident.get("nctId"),
        "brief_title": ident.get("briefTitle"),
        "overall_status": status_mod.get("overallStatus"),
        "phase": design_mod.get("phases") or [],
        "study_type": design_mod.get("studyType"),
        "conditions": cond_mod.get("conditions") or [],
        "interventions": interventions,
        "locations": locations,
        "lead_sponsor": _path(sponsor_mod, "leadSponsor", "name"),
        "start_date": _path(status_mod, "startDateStruct", "date"),
        "completion_date": _path(status_mod, "completionDateStruct", "date"),
        "enrollment": _path(design_mod, "enrollmentInfo", "count"),
        "primary_outcomes": primary_outcomes,
    }


def trim_studies(
    studies: list[dict[str, Any]],
    *,
    max_studies: int,
    max_chars: int,
) -> list[dict[str, Any]]:
    """Trim each study and cap total studies + serialized size.

    Drops studies from the tail until JSON-serialized size fits in `max_chars`.
    """
    trimmed = [trim_study_payload(s) for s in studies[:max_studies]]
    while trimmed and len(json.dumps(trimmed, ensure_ascii=False)) > max_chars:
        dropped = trimmed.pop()
        log.info("trimming.trim_studies dropped study to fit char cap nct_id=%s", dropped.get("nct_id"))
    return trimmed


def extract_nct_ids(studies: list[dict[str, Any]]) -> list[str]:
    """Pull NCT IDs from a list of raw v2 study dicts (pre-trim)."""
    out: list[str] = []
    for s in studies:
        nct = _path(s, "protocolSection", "identificationModule", "nctId")
        if isinstance(nct, str):
            out.append(nct)
    return out

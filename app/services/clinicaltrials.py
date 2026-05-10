from __future__ import annotations

import json
from typing import Any

import httpx

from ..config import Settings
from ..errors import UpstreamBadRequest, UpstreamNotFound, UpstreamUnavailable
from ..logging_config import get_logger

log = get_logger(__name__)


# Mapping of our public query params -> ClinicalTrials.gov v2 `query.*` fields.
# TODO: verify against live API spec at https://clinicaltrials.gov/data-api/api
# Field names follow the v2 "Essie expression" query parameters.
_FILTER_TO_CTGOV: dict[str, str] = {
    "conditions": "query.cond",
    "location": "query.locn",
    "title": "query.titles",
    "intervention": "query.intr",
    "outcome_measure": "query.outc",
    "sponsor": "query.spons",
    "lead": "query.lead",
    "study_id": "query.id",
}


def build_search_params(
    *,
    free_text: str,
    filters: dict[str, str | None],
    page_size: int,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "format": "json",
        "pageSize": page_size,
        "countTotal": "true",
    }
    if free_text:
        params["query.term"] = free_text
    for ours, theirs in _FILTER_TO_CTGOV.items():
        v = filters.get(ours)
        if v:
            params[theirs] = v
    return params


def _coerce_retry_after(resp: httpx.Response) -> int | None:
    ra = resp.headers.get("retry-after")
    if not ra:
        return None
    try:
        return max(0, int(ra))
    except ValueError:
        return None


def _translate_status_error(resp: httpx.Response, *, single_study: bool) -> Exception:
    status = resp.status_code
    body_text = ""
    try:
        body_text = resp.text[:500]
    except Exception:
        pass

    if status == 404 and single_study:
        return UpstreamNotFound("ClinicalTrials.gov: study not found.", details={"upstream_body": body_text})
    if 400 <= status < 500:
        return UpstreamBadRequest(
            f"ClinicalTrials.gov rejected the request ({status}).",
            details={"upstream_status": status, "upstream_body": body_text},
        )
    return UpstreamUnavailable(
        f"ClinicalTrials.gov returned {status}.",
        details={"upstream_status": status, "upstream_body": body_text},
        retry_after=_coerce_retry_after(resp),
    )


async def _request_with_one_retry(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> httpx.Response:
    """Issue one retry on transient network errors only. Never retry on 4xx."""
    try:
        return await client.request(method, url, **kwargs)
    except (httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.PoolTimeout) as e:
        log.warning("ctgov transient network error, retrying once: %s", e)
        return await client.request(method, url, **kwargs)


async def fetch_single_study(
    client: httpx.AsyncClient, settings: Settings, nct_id: str
) -> dict[str, Any]:
    url = f"{settings.ctgov_base_url}/studies/{nct_id}"
    log.info("ctgov.fetch_single_study nct_id=%s", nct_id)
    try:
        resp = await _request_with_one_retry(
            client, "GET", url, params={"format": "json"}
        )
    except httpx.TimeoutException as e:
        raise UpstreamUnavailable(
            "ClinicalTrials.gov request timed out.",
            details={"error": str(e)},
            retry_after=2,
        ) from e
    except httpx.HTTPError as e:
        raise UpstreamUnavailable(
            "ClinicalTrials.gov network error.",
            details={"error": str(e)},
            retry_after=2,
        ) from e

    if resp.status_code >= 400:
        raise _translate_status_error(resp, single_study=True)

    try:
        return resp.json()
    except json.JSONDecodeError as e:
        raise UpstreamUnavailable(
            "ClinicalTrials.gov returned malformed JSON.",
            details={"error": str(e)},
        ) from e


async def search_studies(
    client: httpx.AsyncClient,
    settings: Settings,
    *,
    free_text: str,
    filters: dict[str, str | None],
) -> dict[str, Any]:
    url = f"{settings.ctgov_base_url}/studies"
    params = build_search_params(
        free_text=free_text, filters=filters, page_size=settings.ctgov_page_size
    )
    log.info("ctgov.search_studies params_keys=%s", sorted(params.keys()))
    try:
        resp = await _request_with_one_retry(client, "GET", url, params=params)
    except httpx.TimeoutException as e:
        raise UpstreamUnavailable(
            "ClinicalTrials.gov request timed out.",
            details={"error": str(e)},
            retry_after=2,
        ) from e
    except httpx.HTTPError as e:
        raise UpstreamUnavailable(
            "ClinicalTrials.gov network error.",
            details={"error": str(e)},
            retry_after=2,
        ) from e

    if resp.status_code >= 400:
        raise _translate_status_error(resp, single_study=False)

    try:
        return resp.json()
    except json.JSONDecodeError as e:
        raise UpstreamUnavailable(
            "ClinicalTrials.gov returned malformed JSON.",
            details={"error": str(e)},
        ) from e

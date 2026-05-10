"""LLM-facing schema for translating a natural-language query into a
ClinicalTrials.gov v2 Essie expression for the `query.term` parameter.

Strict-mode rules: every field required, no defaults, no Union beyond X | None,
extra='forbid' so additionalProperties: false everywhere. Lists of strings (and
lists of lists of strings) are fine — strict mode only forbids open objects.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class EssieTranslation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The fully assembled Essie expression to send as `query.term`.
    expression: str

    # AND-joined groups (single quoted phrases or stems). Surfaced for
    # transparency; `expression` is what we actually send.
    must_include: list[str]

    # Synonym/alternative groups OR'd together. Each inner list is one
    # OR-group, e.g. [["pediatric", "paediatric", "child"]]. [] when none.
    should_include: list[list[str]]

    # Terms / phrases excluded with NOT. [] when none.
    must_exclude: list[str]

    # One-line explanation of the translation choices.
    rationale: str

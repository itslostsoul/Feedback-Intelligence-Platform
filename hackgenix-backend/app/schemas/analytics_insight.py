from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --- Input side: the deterministic evidence bundle handed to the LLM. Every
# field here is already computed by app.services.analytics from persisted
# PostgreSQL aggregates — no raw feedback text, no embedding vector, and no
# new calculation happens as part of building this DTO. Same philosophy as
# DecisionSynthesisInput (app.schemas.decision_brief): the LLM only ever sees
# already-persisted/already-computed facts, each with a citation_id.


class AnalyticsEvidenceFact(BaseModel):
    """One citable aggregate fact. `citation_id` follows the
    `analytics:<...>` convention documented in AnalyticsSynthesisInput —
    e.g. `analytics:feedback_volume`, `analytics:category:bug`,
    `analytics:issue:<issue_id>:trend`, `analytics:source:zendesk`."""

    citation_id: str
    label: str
    value: dict | list | str | int | float | bool | None


class AnalyticsSynthesisInput(BaseModel):
    """The complete, deterministic input to one insight-generation attempt.
    Built purely from app.services.analytics's own aggregate functions
    (overview/categories/sources/top-issues/trends) — building this DTO
    involves no LLM call and no raw feedback access."""

    generated_from_calculated_at: datetime
    facts: list[AnalyticsEvidenceFact]

    def citation_registry(self) -> dict[str, AnalyticsEvidenceFact]:
        return {fact.citation_id: fact for fact in self.facts}


# --- Output side: the strict contract every LLMProvider implementation must
# satisfy for analytics synthesis. This is an ANALYTICS INTERPRETER, not a
# decision-maker — there is deliberately no priority/recommendation/roadmap/
# score field anywhere in this contract for the LLM to fill in even if asked.


class InsightClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=400)
    evidence_ids: list[str] = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("evidence_ids")
    @classmethod
    def _evidence_ids_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [c.strip() for c in value if c and c.strip()]
        if not cleaned:
            raise ValueError("must contain at least one non-blank evidence_id")
        return cleaned


class AnalyticsInsightLLMOutput(BaseModel):
    """Mirrors DecisionBriefLLMOutput's role for the analytics workflow.
    Arbitrary prose or partial JSON is never accepted. No recommendation,
    priority, confidence, or score field is defined here."""

    model_config = ConfigDict(extra="forbid")

    headline: str = Field(min_length=1, max_length=400)
    key_findings: list[InsightClaim]
    emerging_patterns: list[InsightClaim]
    areas_to_investigate: list[InsightClaim]
    caveats: list[InsightClaim]

    @field_validator("headline")
    @classmethod
    def _headline_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


# --- API-facing response -------------------------------------------------------


class AnalyticsInsightResult(BaseModel):
    """Full response for POST /analytics/insights, including resolved
    evidence inline so the frontend never needs a second round-trip to
    explain a claim."""

    headline: str
    key_findings: list[InsightClaim]
    emerging_patterns: list[InsightClaim]
    areas_to_investigate: list[InsightClaim]
    caveats: list[InsightClaim]

    provider: str
    model: str
    insight_version: str
    generated_at: datetime

    evidence: dict[str, AnalyticsEvidenceFact]

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models.enums import (
    EvidenceSufficiency,
    FeedbackCategory,
    GenerationStatus,
    MatchMethod,
)
from app.schemas.decision import EvidenceFact

# --- Input side: what gets built from persisted Phase 4A data and handed to
# the LLM provider. Never includes raw feedback text, never includes an
# embedding vector, never includes anything not already persisted by Phase
# 4A (plus read-only ProductIssue title/category/affected_feature, which are
# explicitly contextual labels, not evidence — see SynthesisIssue below).


class SynthesisFact(EvidenceFact):
    """One citable fact, exactly `DecisionIssueEvidence`'s existing
    {code, label, value, threshold, source_signal_path} shape, plus a
    deterministically-assigned `citation_id` and which list it came from.
    `citation_id` is `"{product_issue_id}:{code}"` for issue-scoped facts,
    or `"decision:{code}"` for decision-level gaps — unique within one
    evaluation, since each rule fires at most once per issue per
    evaluation (Phase 4A guarantee)."""

    citation_id: str
    category: Literal["supporting", "counter", "gap"]


class SynthesisIssue(BaseModel):
    """One matched ProductIssue's context + facts, as handed to the LLM.

    `title`/`category`/`affected_feature` are supplied for readability only
    — they are explicitly NOT evidence. A title such as "Checkout payment
    failures" must never by itself license a quantitative or evidentiary
    claim; only facts in `supporting`/`counter`/`gaps` (each with its own
    citation_id) may back such a claim. The synthesis prompt states this
    distinction explicitly (see app.services.llm.gemini_provider)."""

    product_issue_id: uuid.UUID
    title: str
    category: FeedbackCategory | None
    affected_feature: str | None
    match_method: MatchMethod
    match_score: float
    matched_terms: dict
    volume_summary: dict
    supporting: list[SynthesisFact]
    counter: list[SynthesisFact]
    gaps: list[SynthesisFact]


class DecisionSynthesisInput(BaseModel):
    """The complete, deterministic input to one brief-generation attempt.
    Built purely from persisted Phase 4A rows (plus the read-only
    ProductIssue join above) — building this DTO involves no LLM call, no
    new signal computation, and no raw feedback access. Same evaluation_id
    always produces a byte-identical DecisionSynthesisInput."""

    decision_id: uuid.UUID
    decision_evaluation_id: uuid.UUID
    query_text: str
    keywords: list[str]
    category: FeedbackCategory | None
    affected_feature_hint: str | None
    issues: list[SynthesisIssue]
    decision_level_gaps: list[SynthesisFact]

    def citation_registry(self) -> dict[str, SynthesisFact]:
        """Every citable fact in this input, keyed by citation_id — the
        source of truth grounding validation checks LLM output against."""
        registry: dict[str, SynthesisFact] = {}
        for issue in self.issues:
            for fact in (*issue.supporting, *issue.counter, *issue.gaps):
                registry[fact.citation_id] = fact
        for fact in self.decision_level_gaps:
            registry[fact.citation_id] = fact
        return registry


# --- Output side: the strict contract every LLMProvider implementation must
# satisfy for brief synthesis. Deliberately narrow — everything derivable
# without synthesis (affected_issues, evidence_sufficiency, disclaimers) is
# computed by the backend instead, never asked of the LLM. No recommendation,
# priority, confidence, or score field exists anywhere in this contract.


class NarrativeClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=280)
    citation_ids: list[str] = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("citation_ids")
    @classmethod
    def _citation_ids_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [c.strip() for c in value if c and c.strip()]
        if not cleaned:
            raise ValueError("must contain at least one non-blank citation_id")
        return cleaned


class DecisionBriefLLMOutput(BaseModel):
    """The structured contract every LLM provider must satisfy for brief
    synthesis — mirrors FeedbackAnalysisResult's role in Phase 3A. Arbitrary
    prose or partial JSON is never accepted. Only headline + three narrative
    lists — no recommendation/priority/confidence/score field is defined
    here, so the LLM has no field to put one in even if it tried."""

    model_config = ConfigDict(extra="forbid")

    headline: str = Field(min_length=1, max_length=280)
    supporting_narrative: list[NarrativeClaim]
    counter_narrative: list[NarrativeClaim]
    gap_narrative: list[NarrativeClaim]

    @field_validator("headline")
    @classmethod
    def _headline_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


# --- API-facing read schemas -------------------------------------------------


class ResolvedCitation(BaseModel):
    """A citation_id resolved to its full provenance — everything the
    frontend needs to display "why" without understanding the database
    joins behind it, and (for issue-scoped facts) enough to link onward to
    `GET /issues/{product_issue_id}` for the underlying feedback."""

    citation_id: str
    product_issue_id: uuid.UUID | None
    issue_title: str | None
    code: str
    label: str
    value: dict | list | str | int | float | bool | None
    threshold: float | int | None
    source_signal_path: str | None
    category: Literal["supporting", "counter", "gap"]


class DecisionBriefSummary(BaseModel):
    """Lightweight row for the history-list endpoint — no narrative or
    resolved citations (only the single/detail endpoints return those)."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    decision_evaluation_id: uuid.UUID
    status: GenerationStatus
    evidence_sufficiency: EvidenceSufficiency
    provider: str
    model: str
    generation_version: str
    error_code: str | None
    generated_at: datetime
    created_at: datetime


class DecisionBriefListResponse(BaseModel):
    items: list[DecisionBriefSummary]
    total: int
    limit: int
    offset: int


class DecisionBriefDetail(BaseModel):
    """Full detail for one brief, including resolved citations inline so a
    client never needs a second round-trip to explain a claim."""

    id: uuid.UUID
    decision_evaluation_id: uuid.UUID
    status: GenerationStatus
    evidence_sufficiency: EvidenceSufficiency
    headline: str | None
    supporting_narrative: list[NarrativeClaim] | None
    counter_narrative: list[NarrativeClaim] | None
    gap_narrative: list[NarrativeClaim] | None
    provider: str
    model: str
    generation_version: str
    error_code: str | None
    generated_at: datetime
    created_at: datetime
    citations: dict[str, ResolvedCitation]

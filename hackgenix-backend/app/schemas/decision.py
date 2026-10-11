import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.db.models.enums import DecisionStatus, FeedbackCategory, MatchMethod, ProductIssueStatus


class DecisionCreate(BaseModel):
    """Input payload for creating a Decision.

    `keywords` are explicit, caller-provided deterministic matching inputs —
    never derived from `query_text` by an LLM or NLP extraction step (Phase
    4A is fully deterministic; see app.services.decision_matching).
    """

    query_text: str
    keywords: list[str]
    category: FeedbackCategory | None = None
    affected_feature_hint: str | None = Field(default=None, max_length=255)

    @field_validator("query_text")
    @classmethod
    def _query_text_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("keywords")
    @classmethod
    def _keywords_not_empty(cls, value: list[str]) -> list[str]:
        cleaned = [kw.strip() for kw in value if kw and kw.strip()]
        if not cleaned:
            raise ValueError("must contain at least one non-blank keyword")
        return cleaned

    @field_validator("affected_feature_hint")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class DecisionRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    query_text: str
    keywords: list[str]
    category: FeedbackCategory | None
    affected_feature_hint: str | None
    status: DecisionStatus
    created_at: datetime
    updated_at: datetime


class DecisionListResponse(BaseModel):
    items: list[DecisionRead]
    total: int
    limit: int
    offset: int


class EvidenceFact(BaseModel):
    """One deterministic, provenance-preserving evidence fact. `value`
    always records the literal observed data at evaluation time (never a
    reference that needs the current signal snapshot to interpret) —
    `threshold` is the fixed rule threshold that was compared against, or
    null when the rule has no single threshold (e.g. a trend direction)."""

    code: str
    label: str
    value: dict | list | str | int | float | bool | None
    threshold: float | int | None
    source_signal_path: str | None


class DecisionIssueEvidenceRead(BaseModel):
    """`issue_*` fields are read live from ProductIssue at response-build
    time (never stored on DecisionIssueEvidence — Phase 4A never snapshots
    them; see docs/architecture.md's Phase 4C provenance notes) purely for
    frontend display convenience, so a dashboard doesn't need a separate
    GET /issues/{id} round-trip per matched issue. They reflect the issue's
    CURRENT title/category/affected_feature, which may have changed since
    this evaluation was run — only `supporting`/`counter`/`gaps` are
    historically authoritative."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    decision_evaluation_id: uuid.UUID
    product_issue_id: uuid.UUID
    product_issue_signal_snapshot_id: uuid.UUID | None
    match_method: MatchMethod
    match_score: float
    matched_terms: dict
    volume_summary: dict
    supporting: list[EvidenceFact]
    counter: list[EvidenceFact]
    gaps: list[EvidenceFact]
    created_at: datetime

    issue_title: str | None = None
    issue_category: FeedbackCategory | None = None
    issue_affected_feature: str | None = None
    issue_status: ProductIssueStatus | None = None


class DecisionEvaluationRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    decision_id: uuid.UUID
    evaluation_version: str
    reference_at: datetime
    created_at: datetime
    decision_level_gaps: list[EvidenceFact]
    issues: list[DecisionIssueEvidenceRead]


class DecisionEvaluationSummary(BaseModel):
    """Lightweight row for the evaluations-list endpoint — full per-issue
    evidence is only returned by the single-evaluation detail endpoint."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    decision_id: uuid.UUID
    evaluation_version: str
    reference_at: datetime
    created_at: datetime
    decision_level_gaps: list[EvidenceFact]
    matched_issue_count: int


class DecisionEvaluationListResponse(BaseModel):
    items: list[DecisionEvaluationSummary]
    total: int
    limit: int
    offset: int

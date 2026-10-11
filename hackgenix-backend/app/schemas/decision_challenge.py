import uuid
from datetime import datetime

from pydantic import BaseModel

from app.db.models.enums import FeedbackCategory
from app.schemas.decision_stability import ChangeEvent, DecisionStabilityStatus, IssueStabilityStatus


class ChallengeIssueSummary(BaseModel):
    """Current display metadata for one issue referenced anywhere in a
    Challenge response — read live from ProductIssue (see
    DecisionIssueEvidenceRead's docstring for the same current-state vs.
    historically-pinned distinction). Looked up once per issue and shared
    by reference (`product_issue_id`) rather than repeated on every event,
    so the payload doesn't duplicate the same title/category dozens of
    times."""

    product_issue_id: uuid.UUID
    title: str | None
    category: FeedbackCategory | None
    affected_feature: str | None
    status: str | None


class IssueChallengeSummary(BaseModel):
    """One matched issue's rollup status + full event list — identical
    content to Phase 4C's IssueStabilityResult, included here so a
    Challenge response is self-contained without requiring a second call
    to .../stability."""

    product_issue_id: uuid.UUID
    status: IssueStabilityStatus
    events: list[ChangeEvent]


class DecisionChallengeResult(BaseModel):
    """A read-only, reorganized view over Phase 4C's `DecisionStabilityResult`
    — NOT a second change-detection engine. Every event here is produced by
    `app.services.decision_stability.compare_evaluations`/`compare_latest_two`;
    this layer only groups the SAME events by category for easier frontend
    rendering (e.g. a "Challenge My Decision" panel with separate
    supporting/counter/trend/issue-lifecycle/gap sections) and resolves
    `product_issue_id`s to display metadata.

    **This is explicitly not a recommendation engine.** There is no
    ranking, score, "winner", or verdict field anywhere in this schema —
    only categorized, factual evidence changes. `status` is the same
    Phase-4C convenience rollup (never authoritative on its own); `events`
    (inherited via `all_events`) remains the complete, authoritative
    account, exactly as Phase 4C guarantees.
    """

    stability_version: str
    status: DecisionStabilityStatus
    evaluations_available: int

    previous_evaluation_id: uuid.UUID | None
    current_evaluation_id: uuid.UUID | None
    previous_reference_at: datetime | None
    current_reference_at: datetime | None
    previous_evaluation_version: str | None
    current_evaluation_version: str | None
    evaluation_version_mismatch: bool
    reference_at_tie: bool

    # Categorized views — each is a strict subset/regrouping of `all_events`.
    supporting_changes: list[ChangeEvent]
    counter_changes: list[ChangeEvent]
    trend_changes: list[ChangeEvent]
    gap_changes: list[ChangeEvent]
    relevance_changes: list[ChangeEvent]
    value_changes: list[ChangeEvent]
    new_issues: list[ChangeEvent]
    disappeared_issues: list[ChangeEvent]
    decision_level_changes: list[ChangeEvent]

    issues: list[IssueChallengeSummary]
    issue_summaries: dict[str, ChallengeIssueSummary]

    all_events: list[ChangeEvent]

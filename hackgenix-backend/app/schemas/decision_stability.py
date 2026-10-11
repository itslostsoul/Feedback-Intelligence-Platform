import enum
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ChangeEventType(str, enum.Enum):
    """Fixed, machine-readable event vocabulary (Phase 4C) — never free
    prose. This IS the authoritative output of Phase 4C; the summary
    statuses (IssueStabilityStatus/DecisionStabilityStatus) are convenience
    rollups over these events and never replace or hide them."""

    SUPPORTING_FACT_ADDED = "supporting_fact_added"
    SUPPORTING_FACT_REMOVED = "supporting_fact_removed"
    COUNTER_FACT_ADDED = "counter_fact_added"
    COUNTER_FACT_REMOVED = "counter_fact_removed"
    GAP_ADDED = "gap_added"
    GAP_RESOLVED = "gap_resolved"
    DECISION_LEVEL_GAP_ADDED = "decision_level_gap_added"
    DECISION_LEVEL_GAP_RESOLVED = "decision_level_gap_resolved"
    TREND_CHANGED = "trend_changed"
    MATCH_RELEVANCE_CHANGED = "match_relevance_changed"
    VALUE_CHANGED = "value_changed"
    ISSUE_APPEARED = "issue_appeared"
    ISSUE_DISAPPEARED = "issue_disappeared"


class ChangeInterpretation(str, enum.Enum):
    """Fixed vocabulary for how a change is factually characterized — purely
    descriptive, never a positive/negative judgment. In particular,
    "increased"/"decreased" describe numeric direction only (e.g. a
    duplicate_ratio or a trend window count), never a quality verdict —
    Phase 4A's own supporting/counter classification is what already
    carries evidentiary meaning (via which bucket a fact fires into); this
    vocabulary must never re-add a value judgment on top of that."""

    INCREASED = "increased"
    DECREASED = "decreased"
    CHANGED = "changed"
    NOT_EVALUATED = "not_evaluated"
    NEWLY_AVAILABLE = "newly_available"
    UNAVAILABLE = "unavailable"
    NEWLY_PRESENT = "newly_present"
    NO_LONGER_PRESENT = "no_longer_present"
    APPEARED = "appeared"
    DISAPPEARED = "disappeared"
    DIRECTION_REVERSED = "direction_reversed"
    TRANSITIONED = "transitioned"


class IssueStabilityStatus(str, enum.Enum):
    """Convenience rollup of one issue's evidence-pattern movement between
    two evaluations. Describes evidence-pattern movement ONLY — never a
    judgment that the issue's priority, correctness, or importance changed.
    Never overrides or hides the underlying `events` list."""

    NEW = "new"
    REMOVED = "removed"
    NO_CHANGE = "no_change"
    STRENGTHENED = "strengthened"
    WEAKENED = "weakened"
    MIXED = "mixed"


class DecisionStabilityStatus(str, enum.Enum):
    """Decision-level convenience rollup. **STABLE means the evidence
    pattern has not changed under these deterministic rules — it is not a
    judgment that the underlying decision is correct, good, or should be
    acted on.** Never overrides or hides the underlying `events` list."""

    INSUFFICIENT_HISTORY = "insufficient_history"
    STABLE = "stable"
    STRENGTHENED = "strengthened"
    WEAKENED = "weakened"
    MIXED = "mixed"


class ChangeEvent(BaseModel):
    """One deterministic, factual change between two evaluations. `value`
    fields are always the literal data recorded at evaluation time (from
    persisted DecisionIssueEvidence/DecisionEvaluation rows) — never
    re-derived from a live ProductIssueSignalSnapshot. `interpretation` is
    always drawn from the fixed ChangeInterpretation vocabulary, never
    generated text."""

    type: ChangeEventType
    product_issue_id: uuid.UUID | None
    fact_code: str | None
    bucket: Literal["supporting", "counter", "gaps"] | None
    previous_value: dict | list | str | int | float | bool | None
    current_value: dict | list | str | int | float | bool | None
    source_signal_path: str | None
    interpretation: ChangeInterpretation


class IssueStabilityResult(BaseModel):
    """One matched issue's status + the complete, authoritative list of
    events that produced it."""

    product_issue_id: uuid.UUID
    status: IssueStabilityStatus
    events: list[ChangeEvent]


class DecisionStabilityResult(BaseModel):
    """Result of comparing two DecisionEvaluations for one Decision.

    `status` is a convenience summary only — `events` (the flattened union
    of `decision_level_events` and every issue's events) is always the
    complete, authoritative account of what changed and must never be
    empty when `status` is not STABLE/INSUFFICIENT_HISTORY.

    `evaluation_version_mismatch` — set when the two evaluations were
    produced under different Phase 4A rule versions: an apparent change may
    reflect a rule change, not real-world evidence drift.
    `reference_at_tie` — set when the two evaluations share an identical
    `reference_at`; ordering fell back to `id` as a deterministic-but-
    arbitrary tiebreak.
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

    issue_changes: list[IssueStabilityResult]
    decision_level_events: list[ChangeEvent]
    events: list[ChangeEvent]

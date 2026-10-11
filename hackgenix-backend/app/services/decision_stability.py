import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.decision_evaluation import DecisionEvaluation
from app.db.models.decision_issue_evidence import DecisionIssueEvidence
from app.schemas.decision_stability import (
    ChangeEvent,
    ChangeEventType,
    ChangeInterpretation,
    DecisionStabilityResult,
    DecisionStabilityStatus,
    IssueStabilityResult,
    IssueStabilityStatus,
)
from app.services.decision_evidence import get_evidence_for_evaluation

# Bump whenever the comparison algorithm (which fields participate, the
# value-comparison rules) or the ChangeEvent/interpretation vocabulary
# changes — bundles both, same simplification already made for
# DECISION_EVALUATION_VERSION and DECISION_BRIEF_SYNTHESIS_VERSION. Since
# Phase 4C persists nothing (see module docstring below), this version is
# surfaced only in the API response, never stamped on a row.
DECISION_STABILITY_VERSION = "stability-v1"

# --- Trend-state inference ----------------------------------------------------
# Phase 4A never persists a "flat" fact directly — trend state is
# reconstructed, deterministically, purely from which of these three fact
# codes is present on a DecisionIssueEvidence row. None present -> flat.
_TREND_INCREASING_CODE = "increasing_trend"
_TREND_DECREASING_CODE = "declining_trend"
_TREND_INSUFFICIENT_CODE = "insufficient_recent_feedback"

# The only pairs that constitute a genuine directional reversal — pure fact,
# never a positive/negative judgment. A decreasing trend is not treated as
# universally negative, nor increasing as universally positive; Phase 4A's
# own rules already decide which bucket (supporting/counter) a trend fact
# falls into, and Phase 4C never re-derives or overrides that.
_REVERSAL_PAIRS = {("increasing", "decreasing"), ("decreasing", "increasing")}

_APPEARED_EVENT_TYPE = {
    "supporting": ChangeEventType.SUPPORTING_FACT_ADDED,
    "counter": ChangeEventType.COUNTER_FACT_ADDED,
    "gaps": ChangeEventType.GAP_ADDED,
}
_DISAPPEARED_EVENT_TYPE = {
    "supporting": ChangeEventType.SUPPORTING_FACT_REMOVED,
    "counter": ChangeEventType.COUNTER_FACT_REMOVED,
    "gaps": ChangeEventType.GAP_RESOLVED,
}

# Event types that count toward the issue-level strengthened/weakened
# rollup (see _rollup_issue_status). GAP_ADDED/GAP_RESOLVED are
# deliberately excluded — Phase 4A's own design treats insufficient
# evidence as a GAP, never as positive or negative evidence, and Phase 4C
# preserves that: a gap appearing or resolving is never scored as
# "weakened"/"strengthened" on its own.
_POSITIVE_FACT_EVENT_TYPES = {ChangeEventType.SUPPORTING_FACT_ADDED, ChangeEventType.COUNTER_FACT_REMOVED}
_NEGATIVE_FACT_EVENT_TYPES = {ChangeEventType.SUPPORTING_FACT_REMOVED, ChangeEventType.COUNTER_FACT_ADDED}
_GAP_FACT_EVENT_TYPES = {ChangeEventType.GAP_ADDED, ChangeEventType.GAP_RESOLVED}


def infer_trend_state(evidence_row: DecisionIssueEvidence) -> str:
    """Reconstructs "increasing" | "decreasing" | "insufficient" | "flat"
    purely from which of the three trend-related fact codes is present on
    this (already-persisted, immutable) evidence row. Never reads a live
    ProductIssueSignalSnapshot."""
    supporting_codes = {fact["code"] for fact in evidence_row.supporting}
    counter_codes = {fact["code"] for fact in evidence_row.counter}
    gap_codes = {fact["code"] for fact in evidence_row.gaps}

    if _TREND_INCREASING_CODE in supporting_codes:
        return "increasing"
    if _TREND_DECREASING_CODE in counter_codes:
        return "decreasing"
    if _TREND_INSUFFICIENT_CODE in gap_codes:
        return "insufficient"
    return "flat"


def _kind(value) -> str:
    """Classifies a JSON-decoded value's comparison type. bool is checked
    before int/float since bool is a subclass of int in Python."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "structured"  # dict, list, or otherwise not safely comparable


def _interpret_change(previous_value, current_value) -> ChangeInterpretation:
    """Values already known to differ (by `!=`). Numbers get a directional
    interpretation; bool/str get a neutral "changed"; anything structured,
    or a value that changed shape entirely (e.g. str -> number), is
    reported as not_evaluated rather than guessing at meaning — the
    explicit "mark as not comparable instead of inventing semantics" rule.
    """
    previous_kind, current_kind = _kind(previous_value), _kind(current_value)
    if previous_kind != current_kind or previous_kind == "structured":
        return ChangeInterpretation.NOT_EVALUATED
    if previous_kind == "number":
        return ChangeInterpretation.INCREASED if current_value > previous_value else ChangeInterpretation.DECREASED
    return ChangeInterpretation.CHANGED  # bool or string


def _compare_value(
    previous_present: bool, previous_value, current_present: bool, current_value
) -> ChangeInterpretation | None:
    """Returns None if there is nothing to report (both absent, or present
    and exactly equal — equality covers structured values too, so an
    unchanged dict/list produces no event)."""
    if not previous_present and not current_present:
        return None
    if previous_present and not current_present:
        return ChangeInterpretation.UNAVAILABLE
    if current_present and not previous_present:
        return ChangeInterpretation.NEWLY_AVAILABLE
    if previous_value == current_value:
        return None
    return _interpret_change(previous_value, current_value)


def _index_fact_list(product_issue_id: uuid.UUID | None, bucket: str, facts: list[dict]) -> dict[tuple, dict]:
    return {(product_issue_id, bucket, fact["code"]): fact for fact in facts}


def _index_issue_row(row: DecisionIssueEvidence) -> dict[tuple, dict]:
    index: dict[tuple, dict] = {}
    index.update(_index_fact_list(row.product_issue_id, "supporting", row.supporting))
    index.update(_index_fact_list(row.product_issue_id, "counter", row.counter))
    index.update(_index_fact_list(row.product_issue_id, "gaps", row.gaps))
    return index


def _sort_key(identity: tuple) -> tuple:
    product_issue_id, bucket, code = identity
    return (str(product_issue_id), bucket, code)


def _diff_fact_index(
    previous_index: dict[tuple, dict], current_index: dict[tuple, dict]
) -> tuple[list[ChangeEvent], dict[tuple, tuple[dict, dict]]]:
    """Three-way diff over fact identity `(product_issue_id, bucket, code)`
    — reused for both per-issue fact sets and decision_level_gaps (where
    product_issue_id is always None). Returns (appeared/disappeared events,
    facts present in both for further value comparison). Iteration is
    explicitly sorted for reproducible output across repeated calls."""
    events: list[ChangeEvent] = []
    persisted: dict[tuple, tuple[dict, dict]] = {}

    all_identities = sorted(set(previous_index) | set(current_index), key=_sort_key)
    for identity in all_identities:
        product_issue_id, bucket, code = identity
        previous_fact = previous_index.get(identity)
        current_fact = current_index.get(identity)

        if previous_fact is not None and current_fact is not None:
            persisted[identity] = (previous_fact, current_fact)
        elif current_fact is not None:
            event_type = (
                ChangeEventType.DECISION_LEVEL_GAP_ADDED if product_issue_id is None else _APPEARED_EVENT_TYPE[bucket]
            )
            events.append(
                ChangeEvent(
                    type=event_type,
                    product_issue_id=product_issue_id,
                    fact_code=code,
                    bucket=bucket,
                    previous_value=None,
                    current_value=current_fact.get("value"),
                    source_signal_path=current_fact.get("source_signal_path"),
                    interpretation=ChangeInterpretation.NEWLY_PRESENT,
                )
            )
        else:
            event_type = (
                ChangeEventType.DECISION_LEVEL_GAP_RESOLVED
                if product_issue_id is None
                else _DISAPPEARED_EVENT_TYPE[bucket]
            )
            events.append(
                ChangeEvent(
                    type=event_type,
                    product_issue_id=product_issue_id,
                    fact_code=code,
                    bucket=bucket,
                    previous_value=previous_fact.get("value"),
                    current_value=None,
                    source_signal_path=previous_fact.get("source_signal_path"),
                    interpretation=ChangeInterpretation.NO_LONGER_PRESENT,
                )
            )

    return events, persisted


def _value_change_events(identity: tuple, previous_fact: dict, current_fact: dict) -> list[ChangeEvent]:
    """Compares a persisted fact's `value` across two evaluations. `value`
    is a dict of named sub-fields for every fact code in the current rule
    set (confirmed against every _fact() call site in
    app.services.decision_evidence) — each key is compared independently by
    type (see _compare_value/_interpret_change). A bare-scalar `value` is
    handled defensively even though no current fact code produces one."""
    product_issue_id, bucket, code = identity
    previous_value = previous_fact.get("value")
    current_value = current_fact.get("value")
    source_signal_path = current_fact.get("source_signal_path") or previous_fact.get("source_signal_path")

    events: list[ChangeEvent] = []

    if not isinstance(previous_value, dict) and not isinstance(current_value, dict):
        interpretation = _compare_value(True, previous_value, True, current_value)
        if interpretation is not None:
            events.append(
                ChangeEvent(
                    type=ChangeEventType.VALUE_CHANGED,
                    product_issue_id=product_issue_id,
                    fact_code=code,
                    bucket=bucket,
                    previous_value=previous_value,
                    current_value=current_value,
                    source_signal_path=source_signal_path,
                    interpretation=interpretation,
                )
            )
        return events

    previous_dict = previous_value if isinstance(previous_value, dict) else {}
    current_dict = current_value if isinstance(current_value, dict) else {}

    for key in sorted(set(previous_dict) | set(current_dict)):
        previous_present, current_present = key in previous_dict, key in current_dict
        interpretation = _compare_value(
            previous_present, previous_dict.get(key), current_present, current_dict.get(key)
        )
        if interpretation is None:
            continue
        events.append(
            ChangeEvent(
                type=ChangeEventType.VALUE_CHANGED,
                product_issue_id=product_issue_id,
                fact_code=code,
                bucket=bucket,
                previous_value=previous_dict.get(key) if previous_present else None,
                current_value=current_dict.get(key) if current_present else None,
                source_signal_path=f"{source_signal_path}.{key}" if source_signal_path else key,
                interpretation=interpretation,
            )
        )
    return events


def _trend_change_event(
    product_issue_id: uuid.UUID, previous_row: DecisionIssueEvidence, current_row: DecisionIssueEvidence
) -> ChangeEvent | None:
    previous_state = infer_trend_state(previous_row)
    current_state = infer_trend_state(current_row)
    if previous_state == current_state:
        return None

    interpretation = (
        ChangeInterpretation.DIRECTION_REVERSED
        if (previous_state, current_state) in _REVERSAL_PAIRS
        else ChangeInterpretation.TRANSITIONED
    )
    return ChangeEvent(
        type=ChangeEventType.TREND_CHANGED,
        product_issue_id=product_issue_id,
        fact_code=None,
        bucket=None,
        previous_value=previous_state,
        current_value=current_state,
        source_signal_path="trend.direction",
        interpretation=interpretation,
    )


def _match_relevance_events(
    product_issue_id: uuid.UUID, previous_row: DecisionIssueEvidence, current_row: DecisionIssueEvidence
) -> list[ChangeEvent]:
    """match_score is a deterministic relevance/match-strength number, never
    a probability or confidence — reported here strictly as increased /
    decreased / changed, never as a "confidence" change."""
    events: list[ChangeEvent] = []

    if previous_row.match_method != current_row.match_method:
        events.append(
            ChangeEvent(
                type=ChangeEventType.MATCH_RELEVANCE_CHANGED,
                product_issue_id=product_issue_id,
                fact_code=None,
                bucket=None,
                previous_value=previous_row.match_method.value,
                current_value=current_row.match_method.value,
                source_signal_path="match_method",
                interpretation=ChangeInterpretation.CHANGED,
            )
        )

    if previous_row.match_score != current_row.match_score:
        interpretation = (
            ChangeInterpretation.INCREASED
            if current_row.match_score > previous_row.match_score
            else ChangeInterpretation.DECREASED
        )
        events.append(
            ChangeEvent(
                type=ChangeEventType.MATCH_RELEVANCE_CHANGED,
                product_issue_id=product_issue_id,
                fact_code=None,
                bucket=None,
                previous_value=previous_row.match_score,
                current_value=current_row.match_score,
                source_signal_path="match_score",
                interpretation=interpretation,
            )
        )
    return events


def _volume_summary_events(
    product_issue_id: uuid.UUID, previous_row: DecisionIssueEvidence, current_row: DecisionIssueEvidence
) -> list[ChangeEvent]:
    events: list[ChangeEvent] = []
    previous_summary = previous_row.volume_summary or {}
    current_summary = current_row.volume_summary or {}

    for key in sorted(set(previous_summary) | set(current_summary)):
        previous_present, current_present = key in previous_summary, key in current_summary
        interpretation = _compare_value(
            previous_present, previous_summary.get(key), current_present, current_summary.get(key)
        )
        if interpretation is None:
            continue
        events.append(
            ChangeEvent(
                type=ChangeEventType.VALUE_CHANGED,
                product_issue_id=product_issue_id,
                fact_code=None,
                bucket=None,
                previous_value=previous_summary.get(key) if previous_present else None,
                current_value=current_summary.get(key) if current_present else None,
                source_signal_path=f"volume_summary.{key}",
                interpretation=interpretation,
            )
        )
    return events


def _rollup_issue_status(fact_set_events: list[ChangeEvent]) -> IssueStabilityStatus:
    """Deliberately conservative: any gap movement (added or resolved)
    forces at least MIXED, never STRENGTHENED/WEAKENED on its own — gaps
    represent missing/insufficient evidence, not directional evidence (see
    _GAP_FACT_EVENT_TYPES). Match-relevance and raw numeric VALUE_CHANGED
    events never participate in this rollup — they are informational only,
    always present in the full event list regardless."""
    positive = sum(1 for event in fact_set_events if event.type in _POSITIVE_FACT_EVENT_TYPES)
    negative = sum(1 for event in fact_set_events if event.type in _NEGATIVE_FACT_EVENT_TYPES)
    gap_changed = any(event.type in _GAP_FACT_EVENT_TYPES for event in fact_set_events)

    if positive and negative:
        return IssueStabilityStatus.MIXED
    if positive:
        return IssueStabilityStatus.MIXED if gap_changed else IssueStabilityStatus.STRENGTHENED
    if negative:
        return IssueStabilityStatus.MIXED if gap_changed else IssueStabilityStatus.WEAKENED
    return IssueStabilityStatus.MIXED if gap_changed else IssueStabilityStatus.NO_CHANGE


def _compare_issue(
    product_issue_id: uuid.UUID,
    previous_row: DecisionIssueEvidence | None,
    current_row: DecisionIssueEvidence | None,
) -> IssueStabilityResult:
    if previous_row is None and current_row is not None:
        event = ChangeEvent(
            type=ChangeEventType.ISSUE_APPEARED,
            product_issue_id=product_issue_id,
            fact_code=None,
            bucket=None,
            previous_value=None,
            current_value=None,
            source_signal_path=None,
            interpretation=ChangeInterpretation.APPEARED,
        )
        return IssueStabilityResult(product_issue_id=product_issue_id, status=IssueStabilityStatus.NEW, events=[event])

    if previous_row is not None and current_row is None:
        event = ChangeEvent(
            type=ChangeEventType.ISSUE_DISAPPEARED,
            product_issue_id=product_issue_id,
            fact_code=None,
            bucket=None,
            previous_value=None,
            current_value=None,
            source_signal_path=None,
            interpretation=ChangeInterpretation.DISAPPEARED,
        )
        return IssueStabilityResult(
            product_issue_id=product_issue_id, status=IssueStabilityStatus.REMOVED, events=[event]
        )

    # present in both — invariant A/B/C: compares ONLY the two rows'
    # immutable, already-persisted fact JSONB. Never reads
    # ProductIssueSignalSnapshot (live or via the convenience FK), and never
    # reads ProductIssue.title/category/affected_feature (not historically
    # pinned by DecisionIssueEvidence — see docs/architecture.md).
    previous_index = _index_issue_row(previous_row)
    current_index = _index_issue_row(current_row)
    fact_set_events, persisted = _diff_fact_index(previous_index, current_index)

    value_events: list[ChangeEvent] = []
    for identity, (previous_fact, current_fact) in persisted.items():
        value_events.extend(_value_change_events(identity, previous_fact, current_fact))

    trend_event = _trend_change_event(product_issue_id, previous_row, current_row)
    match_events = _match_relevance_events(product_issue_id, previous_row, current_row)
    volume_events = _volume_summary_events(product_issue_id, previous_row, current_row)

    all_events = fact_set_events + value_events + ([trend_event] if trend_event else []) + match_events + volume_events
    status = _rollup_issue_status(fact_set_events)
    return IssueStabilityResult(product_issue_id=product_issue_id, status=status, events=all_events)


def _rollup_decision_status(
    issue_results: list[IssueStabilityResult], decision_level_events: list[ChangeEvent]
) -> DecisionStabilityStatus:
    """Same conservative philosophy as _rollup_issue_status: a decision-
    level gap change, or any issue appearing/disappearing, forces at least
    MIXED — a new issue appearing isn't inherently "strengthening" (it
    could be an unrelated new complaint) and an issue disappearing isn't
    inherently "weakening" (it might have been resolved); Phase 4C does not
    invent a direction for either."""
    decision_gap_event_types = {ChangeEventType.DECISION_LEVEL_GAP_ADDED, ChangeEventType.DECISION_LEVEL_GAP_RESOLVED}
    if any(event.type in decision_gap_event_types for event in decision_level_events):
        return DecisionStabilityStatus.MIXED

    statuses = {result.status for result in issue_results}
    if not statuses:
        return DecisionStabilityStatus.STABLE
    if IssueStabilityStatus.NEW in statuses or IssueStabilityStatus.REMOVED in statuses:
        return DecisionStabilityStatus.MIXED
    if statuses == {IssueStabilityStatus.NO_CHANGE}:
        return DecisionStabilityStatus.STABLE
    if statuses <= {IssueStabilityStatus.NO_CHANGE, IssueStabilityStatus.STRENGTHENED}:
        return DecisionStabilityStatus.STRENGTHENED
    if statuses <= {IssueStabilityStatus.NO_CHANGE, IssueStabilityStatus.WEAKENED}:
        return DecisionStabilityStatus.WEAKENED
    return DecisionStabilityStatus.MIXED


def compare_evaluations(
    db: Session, previous: DecisionEvaluation, current: DecisionEvaluation
) -> DecisionStabilityResult:
    """Deterministically compares two DecisionEvaluations belonging to the
    same Decision. Reads ONLY persisted DecisionIssueEvidence rows (via the
    existing Phase 4A get_evidence_for_evaluation helper) and the two
    DecisionEvaluation rows themselves — never ProductIssueSignalSnapshot,
    never ProductIssue's current mutable fields, never DecisionBrief prose.
    Pure function of immutable inputs: calling this twice with the same two
    evaluation ids always returns byte-identical output, and the result is
    unaffected by any later signal recalculation or snapshot deletion.

    `previous`/`current` are taken exactly as passed — callers decide
    direction (see get_last_two_evaluations for the default "latest vs.
    second-latest by reference_at" ordering; the explicit compare-two-
    evaluations API endpoint instead respects the caller's chosen
    from/to order directly).
    """
    previous_rows = {row.product_issue_id: row for row in get_evidence_for_evaluation(db, previous.id)}
    current_rows = {row.product_issue_id: row for row in get_evidence_for_evaluation(db, current.id)}
    all_issue_ids = sorted(set(previous_rows) | set(current_rows), key=str)

    issue_results = [
        _compare_issue(issue_id, previous_rows.get(issue_id), current_rows.get(issue_id))
        for issue_id in all_issue_ids
    ]

    previous_gap_index = _index_fact_list(None, "gaps", previous.decision_level_gaps)
    current_gap_index = _index_fact_list(None, "gaps", current.decision_level_gaps)
    decision_level_events, decision_level_persisted = _diff_fact_index(previous_gap_index, current_gap_index)
    for identity, (previous_fact, current_fact) in decision_level_persisted.items():
        decision_level_events.extend(_value_change_events(identity, previous_fact, current_fact))

    status = _rollup_decision_status(issue_results, decision_level_events)

    flattened_events = list(decision_level_events)
    for result in issue_results:
        flattened_events.extend(result.events)

    return DecisionStabilityResult(
        stability_version=DECISION_STABILITY_VERSION,
        status=status,
        evaluations_available=2,
        previous_evaluation_id=previous.id,
        current_evaluation_id=current.id,
        previous_reference_at=previous.reference_at,
        current_reference_at=current.reference_at,
        previous_evaluation_version=previous.evaluation_version,
        current_evaluation_version=current.evaluation_version,
        evaluation_version_mismatch=previous.evaluation_version != current.evaluation_version,
        reference_at_tie=previous.reference_at == current.reference_at,
        issue_changes=issue_results,
        decision_level_events=decision_level_events,
        events=flattened_events,
    )


def _insufficient_history_result(evaluations_available: int) -> DecisionStabilityResult:
    return DecisionStabilityResult(
        stability_version=DECISION_STABILITY_VERSION,
        status=DecisionStabilityStatus.INSUFFICIENT_HISTORY,
        evaluations_available=evaluations_available,
        previous_evaluation_id=None,
        current_evaluation_id=None,
        previous_reference_at=None,
        current_reference_at=None,
        previous_evaluation_version=None,
        current_evaluation_version=None,
        evaluation_version_mismatch=False,
        reference_at_tie=False,
        issue_changes=[],
        decision_level_events=[],
        events=[],
    )


def get_last_two_evaluations(db: Session, decision_id: uuid.UUID) -> list[DecisionEvaluation]:
    """Ordered by `reference_at` (never `created_at` — see the ordering
    lesson already applied to DecisionEvaluation/DecisionBrief in Phase
    4A/4B), `id` as a deterministic tiebreak. Returns [current, previous]
    (most recent first), 0-2 rows."""
    stmt = (
        select(DecisionEvaluation)
        .where(DecisionEvaluation.decision_id == decision_id)
        .order_by(DecisionEvaluation.reference_at.desc(), DecisionEvaluation.id.desc())
        .limit(2)
    )
    return list(db.execute(stmt).scalars())


def compare_latest_two(db: Session, decision_id: uuid.UUID) -> DecisionStabilityResult:
    """Default comparison: the two most recent evaluations by reference_at.
    Returns an INSUFFICIENT_HISTORY result (never an error) if fewer than 2
    evaluations exist yet — structurally distinct from a "no changes"
    result (its `events`/`issue_changes` are empty AND
    previous/current_evaluation_id are null, so a consumer can never
    conflate the two)."""
    evaluations = get_last_two_evaluations(db, decision_id)
    if len(evaluations) < 2:
        return _insufficient_history_result(len(evaluations))

    current, previous = evaluations[0], evaluations[1]
    return compare_evaluations(db, previous, current)

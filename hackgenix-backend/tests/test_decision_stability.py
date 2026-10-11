import types
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from app.db.models.decision_evaluation import DecisionEvaluation
from app.db.models.decision_issue_evidence import DecisionIssueEvidence
from app.db.models.enums import FeedbackCategory, MatchMethod, ProductIssueStatus, RelationshipType
from app.db.models.feedback_relationship import FeedbackRelationship
from app.db.models.product_issue import ProductIssue
from app.db.models.product_issue_feedback import ProductIssueFeedback
from app.db.models.product_issue_signal_snapshot import ProductIssueSignalSnapshot
from app.schemas.decision_stability import (
    ChangeEvent,
    ChangeEventType,
    ChangeInterpretation,
    DecisionStabilityStatus,
    IssueStabilityResult,
    IssueStabilityStatus,
)
from app.services.decision_evidence import evaluate_decision, get_evidence_for_evaluation
from app.services.decision_stability import (
    DECISION_STABILITY_VERSION,
    _compare_issue,
    _compare_value,
    _diff_fact_index,
    _index_issue_row,
    _match_relevance_events,
    _rollup_decision_status,
    _rollup_issue_status,
    _trend_change_event,
    _volume_summary_events,
    compare_evaluations,
    compare_latest_two,
    get_last_two_evaluations,
    infer_trend_state,
)
from app.services.issue_signals import calculate_issue_signals

REFERENCE_TIME = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


# ============================ Pure-unit fixtures ============================


def _row(product_issue_id=None, *, supporting=None, counter=None, gaps=None, match_method=MatchMethod.AFFECTED_FEATURE, match_score=0.7, volume_summary=None):
    return types.SimpleNamespace(
        product_issue_id=product_issue_id or uuid.uuid4(),
        supporting=supporting or [],
        counter=counter or [],
        gaps=gaps or [],
        match_method=match_method,
        match_score=match_score,
        volume_summary=volume_summary or {},
    )


def _fact(code, value=None, threshold=None, source_signal_path=None, label=None):
    return {
        "code": code,
        "label": label or code,
        "value": value if value is not None else {},
        "threshold": threshold,
        "source_signal_path": source_signal_path,
    }


def _event(event_type: ChangeEventType) -> ChangeEvent:
    return ChangeEvent(
        type=event_type,
        product_issue_id=None,
        fact_code="x",
        bucket="supporting",
        previous_value=None,
        current_value=None,
        source_signal_path=None,
        interpretation=ChangeInterpretation.CHANGED,
    )


# ================================ _compare_value ================================


def test_compare_value_unchanged_returns_none() -> None:
    assert _compare_value(True, 5, True, 5) is None


def test_compare_value_increased() -> None:
    assert _compare_value(True, 5, True, 8) == ChangeInterpretation.INCREASED


def test_compare_value_decreased() -> None:
    assert _compare_value(True, 8, True, 5) == ChangeInterpretation.DECREASED


def test_compare_value_newly_available() -> None:
    assert _compare_value(False, None, True, 5) == ChangeInterpretation.NEWLY_AVAILABLE


def test_compare_value_unavailable() -> None:
    assert _compare_value(True, 5, False, None) == ChangeInterpretation.UNAVAILABLE


def test_compare_value_both_absent_returns_none() -> None:
    assert _compare_value(False, None, False, None) is None


def test_compare_value_equal_structured_returns_none() -> None:
    assert _compare_value(True, {"a": 1}, True, {"a": 1}) is None


def test_compare_value_differing_structured_not_evaluated() -> None:
    assert _compare_value(True, {"a": 1}, True, {"a": 2}) == ChangeInterpretation.NOT_EVALUATED


def test_compare_value_bool_changed() -> None:
    assert _compare_value(True, True, True, False) == ChangeInterpretation.CHANGED


def test_compare_value_string_changed() -> None:
    assert _compare_value(True, "recent", True, "stale") == ChangeInterpretation.CHANGED


def test_compare_value_type_mismatch_not_evaluated() -> None:
    assert _compare_value(True, "5", True, 5) == ChangeInterpretation.NOT_EVALUATED


def test_compare_value_bool_not_treated_as_number() -> None:
    # bool is a subclass of int in Python -- must not be misclassified as a
    # numeric increase/decrease.
    result = _compare_value(True, False, True, True)
    assert result == ChangeInterpretation.CHANGED


# ============================ _diff_fact_index ================================


def test_diff_fact_index_detects_added_removed_persisted() -> None:
    issue_id = uuid.uuid4()
    previous = _index_issue_row(
        _row(issue_id, supporting=[_fact("high_frequency", {"feedback_count": 5})], counter=[_fact("stale_evidence")])
    )
    current = _index_issue_row(_row(issue_id, supporting=[_fact("high_frequency", {"feedback_count": 8})], counter=[]))

    events, persisted = _diff_fact_index(previous, current)

    event_signatures = {(e.type, e.fact_code) for e in events}
    assert (ChangeEventType.COUNTER_FACT_REMOVED, "stale_evidence") in event_signatures
    assert (issue_id, "supporting", "high_frequency") in persisted


def test_diff_fact_index_decision_level_uses_decision_gap_event_types() -> None:
    from app.services.decision_stability import _index_fact_list

    previous = _index_fact_list(None, "gaps", [_fact("no_matching_issues_found")])
    current = _index_fact_list(None, "gaps", [])

    events, _ = _diff_fact_index(previous, current)

    assert events[0].type == ChangeEventType.DECISION_LEVEL_GAP_RESOLVED
    assert events[0].product_issue_id is None


# ============================ trend inference ================================


def test_infer_trend_state_increasing() -> None:
    assert infer_trend_state(_row(supporting=[_fact("increasing_trend")])) == "increasing"


def test_infer_trend_state_decreasing() -> None:
    assert infer_trend_state(_row(counter=[_fact("declining_trend")])) == "decreasing"


def test_infer_trend_state_insufficient() -> None:
    assert infer_trend_state(_row(gaps=[_fact("insufficient_recent_feedback")])) == "insufficient"


def test_infer_trend_state_flat_when_none_present() -> None:
    assert infer_trend_state(_row()) == "flat"


def test_trend_change_event_direction_reversed_increasing_to_decreasing() -> None:
    issue_id = uuid.uuid4()
    previous = _row(issue_id, supporting=[_fact("increasing_trend")])
    current = _row(issue_id, counter=[_fact("declining_trend")])

    event = _trend_change_event(issue_id, previous, current)

    assert event.interpretation == ChangeInterpretation.DIRECTION_REVERSED
    assert event.previous_value == "increasing"
    assert event.current_value == "decreasing"


def test_trend_change_event_direction_reversed_decreasing_to_increasing() -> None:
    issue_id = uuid.uuid4()
    previous = _row(issue_id, counter=[_fact("declining_trend")])
    current = _row(issue_id, supporting=[_fact("increasing_trend")])

    event = _trend_change_event(issue_id, previous, current)

    assert event.interpretation == ChangeInterpretation.DIRECTION_REVERSED


def test_trend_change_event_flat_to_increasing_is_not_a_reversal() -> None:
    issue_id = uuid.uuid4()
    previous = _row(issue_id)
    current = _row(issue_id, supporting=[_fact("increasing_trend")])

    event = _trend_change_event(issue_id, previous, current)

    assert event.interpretation == ChangeInterpretation.TRANSITIONED


def test_trend_change_event_none_when_unchanged() -> None:
    issue_id = uuid.uuid4()
    previous = _row(issue_id, supporting=[_fact("increasing_trend")])
    current = _row(issue_id, supporting=[_fact("increasing_trend")])

    assert _trend_change_event(issue_id, previous, current) is None


def test_trend_change_event_never_labels_decreasing_as_negative() -> None:
    """Explicit regression guard for the approved-architecture clarification:
    a decreasing trend must never be described with a positive/negative
    judgment word anywhere in the interpretation vocabulary."""
    issue_id = uuid.uuid4()
    previous = _row(issue_id, supporting=[_fact("increasing_trend")])
    current = _row(issue_id, counter=[_fact("declining_trend")])

    event = _trend_change_event(issue_id, previous, current)

    forbidden_words = {"good", "bad", "positive", "negative", "worse", "better", "improved", "worsened"}
    assert event.interpretation.value not in forbidden_words


# ============================ match relevance ================================


def test_match_relevance_events_method_and_score_changed() -> None:
    issue_id = uuid.uuid4()
    previous = _row(issue_id, match_method=MatchMethod.TITLE, match_score=0.5)
    current = _row(issue_id, match_method=MatchMethod.AFFECTED_FEATURE, match_score=0.8)

    events = _match_relevance_events(issue_id, previous, current)

    by_path = {e.source_signal_path: e.interpretation for e in events}
    assert by_path["match_method"] == ChangeInterpretation.CHANGED
    assert by_path["match_score"] == ChangeInterpretation.INCREASED


def test_match_relevance_events_none_when_unchanged() -> None:
    issue_id = uuid.uuid4()
    previous = _row(issue_id, match_method=MatchMethod.TITLE, match_score=0.5)
    current = _row(issue_id, match_method=MatchMethod.TITLE, match_score=0.5)

    assert _match_relevance_events(issue_id, previous, current) == []


# ============================ volume summary ================================


def test_volume_summary_events_increase_and_unavailable() -> None:
    issue_id = uuid.uuid4()
    previous = _row(issue_id, volume_summary={"feedback_count": 5, "duplicate_pair_count": 1})
    current = _row(issue_id, volume_summary={"feedback_count": 9})

    events = _volume_summary_events(issue_id, previous, current)

    by_path = {e.source_signal_path: e.interpretation for e in events}
    assert by_path["volume_summary.feedback_count"] == ChangeInterpretation.INCREASED
    assert by_path["volume_summary.duplicate_pair_count"] == ChangeInterpretation.UNAVAILABLE


# ============================ issue-level rollup ================================


def test_rollup_issue_status_no_change() -> None:
    assert _rollup_issue_status([]) == IssueStabilityStatus.NO_CHANGE


def test_rollup_issue_status_strengthened_supporting_added() -> None:
    assert _rollup_issue_status([_event(ChangeEventType.SUPPORTING_FACT_ADDED)]) == IssueStabilityStatus.STRENGTHENED


def test_rollup_issue_status_strengthened_counter_removed() -> None:
    assert _rollup_issue_status([_event(ChangeEventType.COUNTER_FACT_REMOVED)]) == IssueStabilityStatus.STRENGTHENED


def test_rollup_issue_status_weakened_counter_added() -> None:
    assert _rollup_issue_status([_event(ChangeEventType.COUNTER_FACT_ADDED)]) == IssueStabilityStatus.WEAKENED


def test_rollup_issue_status_weakened_supporting_removed() -> None:
    assert _rollup_issue_status([_event(ChangeEventType.SUPPORTING_FACT_REMOVED)]) == IssueStabilityStatus.WEAKENED


def test_rollup_issue_status_mixed_opposing_movement() -> None:
    events = [_event(ChangeEventType.SUPPORTING_FACT_ADDED), _event(ChangeEventType.COUNTER_FACT_ADDED)]
    assert _rollup_issue_status(events) == IssueStabilityStatus.MIXED


def test_rollup_issue_status_mixed_when_gap_changes_alongside_clean_positive() -> None:
    """A gap resolving alongside a clean supporting-only gain must NOT be
    elevated to STRENGTHENED -- gaps carry no directional weight."""
    events = [_event(ChangeEventType.SUPPORTING_FACT_ADDED), _event(ChangeEventType.GAP_RESOLVED)]
    assert _rollup_issue_status(events) == IssueStabilityStatus.MIXED


def test_rollup_issue_status_mixed_when_only_a_gap_changes() -> None:
    assert _rollup_issue_status([_event(ChangeEventType.GAP_ADDED)]) == IssueStabilityStatus.MIXED


# ============================ decision-level rollup ================================


def test_rollup_decision_status_stable_with_no_issues() -> None:
    assert _rollup_decision_status([], []) == DecisionStabilityStatus.STABLE


def test_rollup_decision_status_stable_all_no_change() -> None:
    results = [IssueStabilityResult(product_issue_id=uuid.uuid4(), status=IssueStabilityStatus.NO_CHANGE, events=[])]
    assert _rollup_decision_status(results, []) == DecisionStabilityStatus.STABLE


def test_rollup_decision_status_mixed_when_issue_is_new() -> None:
    results = [IssueStabilityResult(product_issue_id=uuid.uuid4(), status=IssueStabilityStatus.NEW, events=[])]
    assert _rollup_decision_status(results, []) == DecisionStabilityStatus.MIXED


def test_rollup_decision_status_mixed_when_issue_is_removed() -> None:
    results = [IssueStabilityResult(product_issue_id=uuid.uuid4(), status=IssueStabilityStatus.REMOVED, events=[])]
    assert _rollup_decision_status(results, []) == DecisionStabilityStatus.MIXED


def test_rollup_decision_status_strengthened() -> None:
    results = [
        IssueStabilityResult(product_issue_id=uuid.uuid4(), status=IssueStabilityStatus.NO_CHANGE, events=[]),
        IssueStabilityResult(product_issue_id=uuid.uuid4(), status=IssueStabilityStatus.STRENGTHENED, events=[]),
    ]
    assert _rollup_decision_status(results, []) == DecisionStabilityStatus.STRENGTHENED


def test_rollup_decision_status_mixed_when_decision_level_gap_changes() -> None:
    event = _event(ChangeEventType.DECISION_LEVEL_GAP_ADDED)
    assert _rollup_decision_status([], [event]) == DecisionStabilityStatus.MIXED


# ============================ Integration fixtures ================================


def _create_feedback(client, **overrides) -> dict:
    payload = {"source": "app_store", "raw_text": "The app crashes on checkout."}
    payload.update(overrides)
    return client.post("/api/v1/feedback", json=payload).json()


def _create_issue(
    db_session,
    feedback_ids: list[str],
    *,
    title: str = "Checkout fails during payment",
    affected_feature: str | None = "checkout",
    category: FeedbackCategory | None = None,
    status: ProductIssueStatus = ProductIssueStatus.OPEN,
) -> ProductIssue:
    issue = ProductIssue(
        canonical_feedback_id=uuid.UUID(feedback_ids[0]),
        title=title,
        affected_feature=affected_feature,
        category=category,
        status=status,
        clustering_version="cluster-v1",
    )
    db_session.add(issue)
    db_session.flush()
    for fid in feedback_ids:
        db_session.add(ProductIssueFeedback(product_issue_id=issue.id, feedback_id=uuid.UUID(fid)))
    db_session.commit()
    db_session.refresh(issue)
    return issue


def _create_decision(db_session, keywords=None, category=None, query_text="Should we prioritize fixing checkout?"):
    from app.db.models.decision import Decision

    decision = Decision(query_text=query_text, keywords=keywords or ["checkout"], category=category)
    db_session.add(decision)
    db_session.commit()
    db_session.refresh(decision)
    return decision


def _strong_issue(client, db_session, external_prefix="strong", affected_feature="checkout") -> ProductIssue:
    """5 feedback items, multi-source, recent -> strong Phase 4A evidence
    (mirrors tests/test_decision_brief.py's _sufficient_evidence_setup)."""
    ids = []
    sources = ["app_store", "app_store", "app_store", "zendesk", "zendesk"]
    recent = (REFERENCE_TIME - timedelta(days=1)).isoformat()
    for i in range(5):
        fb = _create_feedback(client, external_id=f"{external_prefix}-{i}", source=sources[i], created_at=recent)
        ids.append(fb["id"])
    issue = _create_issue(db_session, ids, affected_feature=affected_feature)
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    return issue


# ============================ Integration: first evaluation / insufficient history ==


def test_stability_insufficient_history_with_zero_evaluations(db_session) -> None:
    decision = _create_decision(db_session)
    result = compare_latest_two(db_session, decision.id)

    assert result.status == DecisionStabilityStatus.INSUFFICIENT_HISTORY
    assert result.evaluations_available == 0
    assert result.events == []
    assert result.previous_evaluation_id is None
    assert result.stability_version == DECISION_STABILITY_VERSION


def test_stability_insufficient_history_with_one_evaluation(client, db_session) -> None:
    issue = _strong_issue(client, db_session)
    decision = _create_decision(db_session)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    result = compare_latest_two(db_session, decision.id)

    assert result.status == DecisionStabilityStatus.INSUFFICIENT_HISTORY
    assert result.evaluations_available == 1


# ============================ Integration: identical evaluations -> STABLE ==========


def test_stability_identical_evaluations_are_stable(client, db_session) -> None:
    issue = _strong_issue(client, db_session)
    decision = _create_decision(db_session)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    result = compare_latest_two(db_session, decision.id)

    assert result.status == DecisionStabilityStatus.STABLE
    assert result.events == []
    assert result.reference_at_tie is True
    issue_result = next(r for r in result.issue_changes if r.product_issue_id == issue.id)
    assert issue_result.status == IssueStabilityStatus.NO_CHANGE
    assert issue_result.events == []


# ============================ Integration: issue lifecycle ==========================


def test_stability_issue_appeared(client, db_session) -> None:
    issue1 = _strong_issue(client, db_session, external_prefix="i1")
    decision = _create_decision(db_session)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    issue2 = _strong_issue(client, db_session, external_prefix="i2")
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME + timedelta(minutes=1))

    result = compare_latest_two(db_session, decision.id)

    appeared = [e for e in result.events if e.type == ChangeEventType.ISSUE_APPEARED]
    assert len(appeared) == 1
    assert appeared[0].product_issue_id == issue2.id
    issue2_result = next(r for r in result.issue_changes if r.product_issue_id == issue2.id)
    assert issue2_result.status == IssueStabilityStatus.NEW


def test_stability_issue_disappeared(client, db_session) -> None:
    issue = _strong_issue(client, db_session)
    decision = _create_decision(db_session)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    issue.status = ProductIssueStatus.RESOLVED
    db_session.add(issue)
    db_session.commit()
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME + timedelta(minutes=1))

    result = compare_latest_two(db_session, decision.id)

    disappeared = [e for e in result.events if e.type == ChangeEventType.ISSUE_DISAPPEARED]
    assert len(disappeared) == 1
    assert disappeared[0].product_issue_id == issue.id
    issue_result = next(r for r in result.issue_changes if r.product_issue_id == issue.id)
    assert issue_result.status == IssueStabilityStatus.REMOVED


# ============================ Integration: fact-set changes ==========================


def test_stability_supporting_fact_added_and_gap_resolved_on_growth(client, db_session) -> None:
    fb1 = _create_feedback(client, external_id="grow-a", source="app_store")
    fb2 = _create_feedback(client, external_id="grow-b", source="app_store")
    issue = _create_issue(db_session, [fb1["id"], fb2["id"]])
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    decision = _create_decision(db_session)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    for i in range(3):
        fb = _create_feedback(client, external_id=f"grow-extra-{i}", source="zendesk")
        db_session.add(ProductIssueFeedback(product_issue_id=issue.id, feedback_id=uuid.UUID(fb["id"])))
    db_session.commit()
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME + timedelta(minutes=1))

    result = compare_latest_two(db_session, decision.id)
    event_types = {e.type for e in result.events}
    assert ChangeEventType.SUPPORTING_FACT_ADDED in event_types
    assert ChangeEventType.GAP_RESOLVED in event_types


def test_stability_counter_fact_added_via_duplicate_concentration(client, db_session) -> None:
    fbs = [_create_feedback(client, external_id=f"dup-{i}", source="app_store") for i in range(6)]
    ids = [fb["id"] for fb in fbs]
    issue = _create_issue(db_session, ids)
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    decision = _create_decision(db_session)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    for a, b in [(0, 1), (0, 2), (1, 2), (3, 4)]:
        id_a, id_b = uuid.UUID(ids[a]), uuid.UUID(ids[b])
        lo, hi = (id_a, id_b) if id_a < id_b else (id_b, id_a)
        db_session.add(
            FeedbackRelationship(
                feedback_id_a=lo, feedback_id_b=hi, relationship_type=RelationshipType.DUPLICATE,
                similarity_score=0.95, algorithm_version="semantic-v1",
            )
        )
    db_session.commit()
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME + timedelta(minutes=1))

    result = compare_latest_two(db_session, decision.id)
    assert ChangeEventType.COUNTER_FACT_ADDED in {e.type for e in result.events}


def test_stability_supporting_removed_counter_added_via_staleness(client, db_session) -> None:
    issue = _strong_issue(client, db_session)
    decision = _create_decision(db_session)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    later = REFERENCE_TIME + timedelta(days=90)
    calculate_issue_signals(db_session, issue, reference_time=later)
    evaluate_decision(db_session, decision, reference_time=later)

    result = compare_latest_two(db_session, decision.id)
    event_types = {e.type for e in result.events}
    assert ChangeEventType.SUPPORTING_FACT_REMOVED in event_types
    assert ChangeEventType.COUNTER_FACT_ADDED in event_types
    issue_result = next(r for r in result.issue_changes if r.product_issue_id == issue.id)
    assert issue_result.status == IssueStabilityStatus.MIXED  # gap movement (trend->insufficient) forces MIXED


def test_stability_decision_level_gap_resolved(client, db_session) -> None:
    decision = _create_decision(db_session, keywords=["nonexistent_xyz"])
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    fb = _create_feedback(client, external_id="dlg", source="app_store")
    issue = _create_issue(db_session, [fb["id"]], affected_feature="nonexistent_xyz")
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME + timedelta(minutes=1))

    result = compare_latest_two(db_session, decision.id)
    assert ChangeEventType.DECISION_LEVEL_GAP_RESOLVED in {e.type for e in result.decision_level_events}
    assert result.status == DecisionStabilityStatus.MIXED


# ============================ Integration: numeric / structured values ===============


def test_stability_numeric_value_changed_without_fact_change(client, db_session) -> None:
    recent = (REFERENCE_TIME - timedelta(days=1)).isoformat()
    ids = []
    for i in range(6):
        fb = _create_feedback(client, external_id=f"num-{i}", source="app_store", created_at=recent)
        ids.append(fb["id"])
    issue = _create_issue(db_session, ids)
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    decision = _create_decision(db_session)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    fb_extra = _create_feedback(client, external_id="num-extra", source="app_store", created_at=recent)
    db_session.add(ProductIssueFeedback(product_issue_id=issue.id, feedback_id=uuid.UUID(fb_extra["id"])))
    db_session.commit()
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME + timedelta(minutes=1))

    result = compare_latest_two(db_session, decision.id)
    value_events = [
        e for e in result.events if e.type == ChangeEventType.VALUE_CHANGED and e.source_signal_path == "volume_summary.feedback_count"
    ]
    assert len(value_events) == 1
    assert value_events[0].interpretation == ChangeInterpretation.INCREASED
    assert value_events[0].previous_value == 6
    assert value_events[0].current_value == 7

    fact_set_types = {
        ChangeEventType.SUPPORTING_FACT_ADDED, ChangeEventType.SUPPORTING_FACT_REMOVED,
        ChangeEventType.COUNTER_FACT_ADDED, ChangeEventType.COUNTER_FACT_REMOVED,
        ChangeEventType.GAP_ADDED, ChangeEventType.GAP_RESOLVED,
    }
    assert not any(e.type in fact_set_types for e in result.events)
    issue_result = next(r for r in result.issue_changes if r.product_issue_id == issue.id)
    assert issue_result.status == IssueStabilityStatus.NO_CHANGE


def test_stability_structured_value_change_marked_not_evaluated(client, db_session) -> None:
    tiers = ["enterprise", "enterprise", "free"]
    ids = [_create_feedback(client, external_id=f"seg-{i}", customer_tier=tier)["id"] for i, tier in enumerate(tiers)]
    issue = _create_issue(db_session, ids)
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    decision = _create_decision(db_session)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    fb_new = _create_feedback(client, external_id="seg-new", customer_tier="pro")
    db_session.add(ProductIssueFeedback(product_issue_id=issue.id, feedback_id=uuid.UUID(fb_new["id"])))
    db_session.commit()
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME + timedelta(minutes=1))

    result = compare_latest_two(db_session, decision.id)
    segment_events = [
        e for e in result.events if e.fact_code == "multiple_customer_segments" and e.type == ChangeEventType.VALUE_CHANGED
    ]
    assert len(segment_events) >= 1
    assert all(e.interpretation == ChangeInterpretation.NOT_EVALUATED for e in segment_events)


# ============================ Integration: trend transitions ===============


def test_stability_trend_direction_reversal_increasing_to_decreasing(client, db_session) -> None:
    t_a = REFERENCE_TIME
    ids = []
    for offset in (1, 2, 3):  # current window @ t_a
        fb = _create_feedback(client, external_id=f"tr-a-{offset}", created_at=(t_a - timedelta(days=offset)).isoformat())
        ids.append(fb["id"])
    fb_prev = _create_feedback(client, external_id="tr-a-prev", created_at=(t_a - timedelta(days=10)).isoformat())
    ids.append(fb_prev["id"])
    issue = _create_issue(db_session, ids)
    calculate_issue_signals(db_session, issue, reference_time=t_a)
    decision = _create_decision(db_session)
    evaluate_decision(db_session, decision, reference_time=t_a)

    t_b = t_a + timedelta(days=8)
    for offset in (1, 2):  # current window @ t_b
        fb = _create_feedback(client, external_id=f"tr-b-{offset}", created_at=(t_b - timedelta(days=offset)).isoformat())
        db_session.add(ProductIssueFeedback(product_issue_id=issue.id, feedback_id=uuid.UUID(fb["id"])))
    db_session.commit()
    calculate_issue_signals(db_session, issue, reference_time=t_b)
    evaluate_decision(db_session, decision, reference_time=t_b)

    result = compare_latest_two(db_session, decision.id)
    trend_events = [e for e in result.events if e.type == ChangeEventType.TREND_CHANGED]
    assert len(trend_events) == 1
    assert trend_events[0].previous_value == "increasing"
    assert trend_events[0].current_value == "decreasing"
    assert trend_events[0].interpretation == ChangeInterpretation.DIRECTION_REVERSED


def test_stability_trend_insufficient_to_valid(client, db_session) -> None:
    fb = _create_feedback(client, external_id="ins-1", created_at=(REFERENCE_TIME - timedelta(days=1)).isoformat())
    issue = _create_issue(db_session, [fb["id"]])
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    decision = _create_decision(db_session)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    for i in range(3):
        extra = _create_feedback(
            client, external_id=f"ins-extra-{i}", created_at=(REFERENCE_TIME - timedelta(days=1)).isoformat()
        )
        db_session.add(ProductIssueFeedback(product_issue_id=issue.id, feedback_id=uuid.UUID(extra["id"])))
    db_session.commit()
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME + timedelta(minutes=1))

    result = compare_latest_two(db_session, decision.id)
    trend_events = [e for e in result.events if e.type == ChangeEventType.TREND_CHANGED]
    assert len(trend_events) == 1
    assert trend_events[0].previous_value == "insufficient"
    assert trend_events[0].current_value == "increasing"
    assert trend_events[0].interpretation == ChangeInterpretation.TRANSITIONED


def test_stability_trend_unchanged_produces_no_trend_event(client, db_session) -> None:
    issue = _strong_issue(client, db_session)
    decision = _create_decision(db_session)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    result = compare_latest_two(db_session, decision.id)
    assert not any(e.type == ChangeEventType.TREND_CHANGED for e in result.events)


# ============================ Integration: provenance guarantees ===============


def test_stability_unaffected_by_signal_snapshot_recalculation_or_deletion(client, db_session) -> None:
    issue = _strong_issue(client, db_session)
    decision = _create_decision(db_session)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    before = compare_latest_two(db_session, decision.id)

    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME + timedelta(days=200))
    after_recalc = compare_latest_two(db_session, decision.id)
    assert after_recalc.model_dump() == before.model_dump()

    db_session.execute(delete(ProductIssueSignalSnapshot).where(ProductIssueSignalSnapshot.product_issue_id == issue.id))
    db_session.commit()
    after_delete = compare_latest_two(db_session, decision.id)
    assert after_delete.model_dump() == before.model_dump()


def test_stability_repeated_comparison_is_deterministic(client, db_session) -> None:
    _strong_issue(client, db_session)
    decision = _create_decision(db_session)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME + timedelta(hours=1))

    first = compare_latest_two(db_session, decision.id)
    second = compare_latest_two(db_session, decision.id)
    assert first.model_dump() == second.model_dump()


# ============================ Integration: versioning / ordering ===============


def test_stability_evaluation_version_mismatch_flag(client, db_session) -> None:
    issue = _strong_issue(client, db_session)
    decision = _create_decision(db_session)
    first = evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    original_evidence = get_evidence_for_evaluation(db_session, first.id)[0]
    second = DecisionEvaluation(
        decision_id=decision.id,
        evaluation_version="decision-evidence-v2-hypothetical",
        reference_at=REFERENCE_TIME + timedelta(hours=1),
        decision_level_gaps=[],
    )
    db_session.add(second)
    db_session.flush()
    db_session.add(
        DecisionIssueEvidence(
            decision_evaluation_id=second.id,
            product_issue_id=original_evidence.product_issue_id,
            product_issue_signal_snapshot_id=original_evidence.product_issue_signal_snapshot_id,
            match_method=original_evidence.match_method,
            match_score=original_evidence.match_score,
            matched_terms=original_evidence.matched_terms,
            volume_summary=original_evidence.volume_summary,
            supporting=original_evidence.supporting,
            counter=original_evidence.counter,
            gaps=original_evidence.gaps,
        )
    )
    db_session.commit()

    result = compare_latest_two(db_session, decision.id)
    assert result.evaluation_version_mismatch is True
    assert result.previous_evaluation_version != result.current_evaluation_version


def test_stability_reference_at_tie_flag(client, db_session) -> None:
    _strong_issue(client, db_session)
    decision = _create_decision(db_session)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    result = compare_latest_two(db_session, decision.id)
    assert result.reference_at_tie is True


def test_stability_out_of_order_reference_at_still_orders_correctly(client, db_session) -> None:
    _strong_issue(client, db_session)
    decision = _create_decision(db_session)

    later_eval = evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME + timedelta(days=10))
    earlier_eval = evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    evaluations = get_last_two_evaluations(db_session, decision.id)
    assert evaluations[0].id == later_eval.id
    assert evaluations[1].id == earlier_eval.id


# ================================= API ============================


def test_stability_endpoint_insufficient_history(client, db_session) -> None:
    decision = _create_decision(db_session)
    response = client.get(f"/api/v1/decisions/{decision.id}/stability")
    assert response.status_code == 200
    assert response.json()["status"] == "insufficient_history"


def test_stability_endpoint_404_missing_decision(client) -> None:
    response = client.get(f"/api/v1/decisions/{uuid.uuid4()}/stability")
    assert response.status_code == 404


def test_stability_endpoint_end_to_end(client, db_session) -> None:
    _strong_issue(client, db_session)
    decision = _create_decision(db_session)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    response = client.get(f"/api/v1/decisions/{decision.id}/stability")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "stable"
    assert body["events"] == []
    assert body["stability_version"] == DECISION_STABILITY_VERSION


def test_compare_endpoint_end_to_end(client, db_session) -> None:
    _strong_issue(client, db_session)
    decision = _create_decision(db_session)
    first = evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)
    second = evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    response = client.get(f"/api/v1/decisions/{decision.id}/evaluations/{first.id}/compare/{second.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["previous_evaluation_id"] == str(first.id)
    assert body["current_evaluation_id"] == str(second.id)


def test_compare_endpoint_404_missing_evaluations(client, db_session) -> None:
    decision = _create_decision(db_session)
    response = client.get(
        f"/api/v1/decisions/{decision.id}/evaluations/{uuid.uuid4()}/compare/{uuid.uuid4()}"
    )
    assert response.status_code == 404


def test_compare_endpoint_404_evaluation_belongs_to_different_decision(client, db_session) -> None:
    _strong_issue(client, db_session, external_prefix="da")
    decision_a = _create_decision(db_session, query_text="Decision A")
    eval_a = evaluate_decision(db_session, decision_a, reference_time=REFERENCE_TIME)

    _strong_issue(client, db_session, external_prefix="db")
    decision_b = _create_decision(db_session, query_text="Decision B")
    eval_b = evaluate_decision(db_session, decision_b, reference_time=REFERENCE_TIME)

    response = client.get(f"/api/v1/decisions/{decision_a.id}/evaluations/{eval_a.id}/compare/{eval_b.id}")
    assert response.status_code == 404


def test_openapi_stability_routes_registered(client) -> None:
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/api/v1/decisions/{decision_id}/stability" in paths
    assert "/api/v1/decisions/{decision_id}/evaluations/{from_evaluation_id}/compare/{to_evaluation_id}" in paths

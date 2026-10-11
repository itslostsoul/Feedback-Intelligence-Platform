import types
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.db.models.decision import Decision
from app.db.models.decision_evaluation import DecisionEvaluation
from app.db.models.decision_issue_evidence import DecisionIssueEvidence
from app.db.models.enums import FeedbackCategory, MatchMethod, ProductIssueStatus
from app.db.models.product_issue import ProductIssue
from app.db.models.product_issue_feedback import ProductIssueFeedback
from app.db.models.product_issue_signal_snapshot import ProductIssueSignalSnapshot
from app.services.decision_evidence import (
    DECISION_EVALUATION_VERSION,
    HIGH_FREQUENCY_THRESHOLD,
    HIGH_SEVERITY_FRACTION_THRESHOLD,
    MIN_CUSTOMER_REACH_THRESHOLD,
    MIN_VOLUME_FOR_JUDGEMENT,
    _counter_facts,
    _gap_facts,
    _supporting_facts,
    evaluate_decision,
    get_evaluation,
    get_latest_evaluation,
    list_evaluations,
)
from app.services.decision_matching import MIN_MATCH_SCORE, find_matching_issues, match_issue
from app.services.issue_signals import (
    DUPLICATE_CONCENTRATION_MODERATE_MAX,
    MIN_SUFFICIENT_SOURCES,
    RECENCY_STALE_DAYS,
    calculate_issue_signals,
)

REFERENCE_TIME = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


# --- Fake-snapshot builders for pure unit tests of evidence classification ----
# Mirrors tests/test_issue_discovery.py's use of types.SimpleNamespace fakes
# for pure-logic unit tests — no DB needed to test classification rules.


def _severity(**overrides) -> dict:
    base = {
        "distribution": {},
        "buckets": {"low": 0, "medium": 0, "high": 0, "critical": 0},
        "mean": None,
        "median": None,
        "analyzed_count": 5,
        "unanalyzed_count": 0,
    }
    base.update(overrides)
    return base


def _trend(**overrides) -> dict:
    base = {
        "window_days": 7,
        "current_window_count": 2,
        "previous_window_count": 2,
        "sufficient_data": True,
        "min_sample_required": 4,
        "direction": "flat",
    }
    base.update(overrides)
    return base


def _segment_coverage(**overrides) -> dict:
    base = {"customer_tier_distribution": {}, "unknown_tier_count": 5}
    base.update(overrides)
    return base


def _evidence_quality(**overrides) -> dict:
    base = {
        "source_diversity_sufficient": False,
        "customer_reach_known": False,
        "severity_data_available": True,
        "severity_analyzed_fraction": 1.0,
        "duplicate_concentration": "insufficient_data",
        "recency": "unknown",
        "segment_coverage_known": False,
        "evaluated_at": REFERENCE_TIME.isoformat(),
    }
    base.update(overrides)
    return base


def _source_diversity(**overrides) -> dict:
    base = {"distinct_sources": 1, "source_distribution": {"app_store": 5}, "source_type_distribution": {}}
    base.update(overrides)
    return base


def _customer_reach(**overrides) -> dict:
    base = {"distinct_known_customers": 0, "known_feedback_count": 0, "anonymous_feedback_count": 5}
    base.update(overrides)
    return base


def _duplicate_ratio(**overrides) -> dict:
    base = {
        "duplicate_pair_count": 0,
        "related_pair_count": 0,
        "total_relationship_pairs": 0,
        "possible_pairs": 10,
        "duplicate_ratio": None,
        "relationship_coverage": None,
    }
    base.update(overrides)
    return base


def _recency(**overrides) -> dict:
    base = {
        "reference_at": REFERENCE_TIME.isoformat(),
        "latest_feedback_at": (REFERENCE_TIME - timedelta(days=10)).isoformat(),
        "oldest_feedback_at": (REFERENCE_TIME - timedelta(days=20)).isoformat(),
        "days_since_latest": 10,
        "days_since_oldest": 20,
    }
    base.update(overrides)
    return base


def _fake_snapshot(*, feedback_count: int = 5, **overrides) -> types.SimpleNamespace:
    base = {
        "feedback_count": feedback_count,
        "severity": _severity(),
        "trend": _trend(),
        "segment_coverage": _segment_coverage(),
        "evidence_quality": _evidence_quality(),
        "source_diversity": _source_diversity(),
        "customer_reach": _customer_reach(),
        "duplicate_ratio": _duplicate_ratio(),
        "recency": _recency(),
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _codes(facts: list[dict]) -> set[str]:
    return {f["code"] for f in facts}


# ============================= SUPPORTING rules ================================


def test_high_frequency_fires_at_threshold() -> None:
    snapshot = _fake_snapshot(feedback_count=HIGH_FREQUENCY_THRESHOLD)
    assert "high_frequency" in _codes(_supporting_facts(snapshot))


def test_high_frequency_does_not_fire_below_threshold() -> None:
    snapshot = _fake_snapshot(feedback_count=HIGH_FREQUENCY_THRESHOLD - 1)
    assert "high_frequency" not in _codes(_supporting_facts(snapshot))


def test_high_severity_concentration_fires_above_fraction_threshold() -> None:
    snapshot = _fake_snapshot(
        severity=_severity(analyzed_count=4, buckets={"low": 0, "medium": 1, "high": 2, "critical": 1})
    )
    facts = _supporting_facts(snapshot)
    fact = next(f for f in facts if f["code"] == "high_severity_concentration")
    assert fact["value"]["severe_fraction"] == 0.75
    assert fact["threshold"] == HIGH_SEVERITY_FRACTION_THRESHOLD


def test_high_severity_concentration_does_not_fire_with_no_analysis() -> None:
    snapshot = _fake_snapshot(severity=_severity(analyzed_count=0, unanalyzed_count=5))
    assert "high_severity_concentration" not in _codes(_supporting_facts(snapshot))


def test_increasing_trend_fires_and_preserves_exact_provenance_shape() -> None:
    snapshot = _fake_snapshot(
        trend=_trend(sufficient_data=True, direction="increasing", current_window_count=8, previous_window_count=3)
    )
    fact = next(f for f in _supporting_facts(snapshot) if f["code"] == "increasing_trend")
    assert fact["value"] == {"direction": "increasing", "current_window_count": 8, "previous_window_count": 3}
    assert fact["threshold"] is None
    assert fact["source_signal_path"] == "trend.direction"


def test_multiple_customer_segments_fires_with_two_or_more_tiers() -> None:
    snapshot = _fake_snapshot(segment_coverage=_segment_coverage(customer_tier_distribution={"enterprise": 3, "free": 2}))
    assert "multiple_customer_segments" in _codes(_supporting_facts(snapshot))


def test_multiple_independent_sources_fires_and_uses_min_sufficient_sources_threshold() -> None:
    snapshot = _fake_snapshot(evidence_quality=_evidence_quality(source_diversity_sufficient=True))
    fact = next(f for f in _supporting_facts(snapshot) if f["code"] == "multiple_independent_sources")
    assert fact["threshold"] == MIN_SUFFICIENT_SOURCES


def test_broad_known_customer_reach_fires_at_threshold() -> None:
    snapshot = _fake_snapshot(customer_reach=_customer_reach(distinct_known_customers=MIN_CUSTOMER_REACH_THRESHOLD))
    assert "broad_known_customer_reach" in _codes(_supporting_facts(snapshot))


def test_sustained_recency_fires_when_recent() -> None:
    snapshot = _fake_snapshot(evidence_quality=_evidence_quality(recency="recent"))
    fact = next(f for f in _supporting_facts(snapshot) if f["code"] == "sustained_recency")
    assert fact["threshold"] == RECENCY_STALE_DAYS


# ============================== COUNTER rules ===================================


def test_single_source_concentration_is_counter_with_sufficient_volume() -> None:
    """Matches the spec's exact example: 10+ items from one source is
    enough volume to trust the measurement, so it's COUNTER."""
    snapshot = _fake_snapshot(feedback_count=10, source_diversity=_source_diversity(distinct_sources=1))
    assert "single_source_concentration" in _codes(_counter_facts(snapshot))


def test_single_source_concentration_is_gap_not_counter_with_insufficient_volume() -> None:
    """Matches the spec's exact example: 2 items from one source is not
    enough volume to judge — GAP, never COUNTER."""
    snapshot = _fake_snapshot(feedback_count=2, source_diversity=_source_diversity(distinct_sources=1))
    assert "single_source_concentration" not in _codes(_counter_facts(snapshot))
    assert "insufficient_source_diversity_data" in _codes(_gap_facts(snapshot))


def test_high_duplicate_concentration_is_counter() -> None:
    snapshot = _fake_snapshot(evidence_quality=_evidence_quality(duplicate_concentration="high"))
    fact = next(f for f in _counter_facts(snapshot) if f["code"] == "high_duplicate_concentration")
    assert fact["threshold"] == DUPLICATE_CONCENTRATION_MODERATE_MAX


def test_stale_evidence_is_counter() -> None:
    snapshot = _fake_snapshot(evidence_quality=_evidence_quality(recency="stale"))
    assert "stale_evidence" in _codes(_counter_facts(snapshot))


def test_low_known_customer_reach_is_counter_with_sufficient_volume() -> None:
    snapshot = _fake_snapshot(feedback_count=10, customer_reach=_customer_reach(distinct_known_customers=0))
    assert "low_known_customer_reach" in _codes(_counter_facts(snapshot))


def test_low_known_customer_reach_is_not_counter_with_insufficient_volume() -> None:
    snapshot = _fake_snapshot(feedback_count=1, customer_reach=_customer_reach(distinct_known_customers=0))
    assert "low_known_customer_reach" not in _codes(_counter_facts(snapshot))


def test_declining_trend_is_counter_only_with_sufficient_data() -> None:
    snapshot = _fake_snapshot(
        trend=_trend(sufficient_data=True, direction="decreasing", current_window_count=1, previous_window_count=5)
    )
    assert "declining_trend" in _codes(_counter_facts(snapshot))


def test_declining_trend_does_not_fire_when_data_insufficient() -> None:
    snapshot = _fake_snapshot(
        trend=_trend(sufficient_data=False, direction="decreasing", current_window_count=1, previous_window_count=1)
    )
    assert "declining_trend" not in _codes(_counter_facts(snapshot))


def test_flat_trend_is_never_counter_evidence() -> None:
    snapshot = _fake_snapshot(
        trend=_trend(sufficient_data=True, direction="flat", current_window_count=3, previous_window_count=3)
    )
    counter_codes = _codes(_counter_facts(snapshot))
    assert "declining_trend" not in counter_codes
    assert not any("trend" in code for code in counter_codes)


def test_flat_trend_is_not_supporting_either() -> None:
    snapshot = _fake_snapshot(
        trend=_trend(sufficient_data=True, direction="flat", current_window_count=3, previous_window_count=3)
    )
    assert "increasing_trend" not in _codes(_supporting_facts(snapshot))


# ================================ GAP rules =====================================


def test_insufficient_recent_feedback_gap_when_trend_insufficient() -> None:
    snapshot = _fake_snapshot(trend=_trend(sufficient_data=False))
    fact = next(f for f in _gap_facts(snapshot) if f["code"] == "insufficient_recent_feedback")
    assert fact["threshold"] == 4


def test_insufficient_recent_feedback_gap_absent_when_trend_sufficient() -> None:
    snapshot = _fake_snapshot(trend=_trend(sufficient_data=True))
    assert "insufficient_recent_feedback" not in _codes(_gap_facts(snapshot))


def test_low_volume_overall_gap_at_boundary() -> None:
    snapshot = _fake_snapshot(feedback_count=MIN_VOLUME_FOR_JUDGEMENT - 1)
    assert "low_volume_overall" in _codes(_gap_facts(snapshot))
    snapshot_ok = _fake_snapshot(feedback_count=MIN_VOLUME_FOR_JUDGEMENT)
    assert "low_volume_overall" not in _codes(_gap_facts(snapshot_ok))


def test_missing_customer_segmentation_gap_when_all_unknown() -> None:
    snapshot = _fake_snapshot(feedback_count=5, segment_coverage=_segment_coverage(unknown_tier_count=5))
    assert "missing_customer_segmentation" in _codes(_gap_facts(snapshot))


def test_missing_customer_segmentation_absent_when_some_known() -> None:
    snapshot = _fake_snapshot(
        feedback_count=5,
        segment_coverage=_segment_coverage(customer_tier_distribution={"enterprise": 2}, unknown_tier_count=3),
    )
    assert "missing_customer_segmentation" not in _codes(_gap_facts(snapshot))


def test_severity_not_analyzed_gap() -> None:
    snapshot = _fake_snapshot(feedback_count=5, severity=_severity(analyzed_count=0, unanalyzed_count=5))
    assert "severity_not_analyzed" in _codes(_gap_facts(snapshot))


def test_severity_not_analyzed_absent_when_some_analyzed() -> None:
    snapshot = _fake_snapshot(feedback_count=5, severity=_severity(analyzed_count=1, unanalyzed_count=4))
    assert "severity_not_analyzed" not in _codes(_gap_facts(snapshot))


def test_no_gaps_when_evidence_is_strong() -> None:
    snapshot = _fake_snapshot(
        feedback_count=10,
        trend=_trend(sufficient_data=True, direction="increasing", current_window_count=7, previous_window_count=3),
        segment_coverage=_segment_coverage(customer_tier_distribution={"enterprise": 5}, unknown_tier_count=5),
        severity=_severity(analyzed_count=10, unanalyzed_count=0),
    )
    assert _gap_facts(snapshot) == []


# ============================ Deterministic matching ============================


def _decision(keywords, category=None):
    return types.SimpleNamespace(keywords=keywords, category=category)


def _issue(affected_feature, title, category=None):
    return types.SimpleNamespace(id=uuid.uuid4(), affected_feature=affected_feature, title=title, category=category)


def test_exact_affected_feature_match() -> None:
    candidate = match_issue(_decision(["checkout"]), _issue("checkout", "Users report a problem during payment"))
    assert candidate is not None
    assert candidate.matched_terms["affected_feature_match_kind"] == "exact"
    assert candidate.method == MatchMethod.AFFECTED_FEATURE
    assert candidate.score == 1.0


def test_case_insensitive_affected_feature_match() -> None:
    candidate = match_issue(_decision(["CheckOut"]), _issue("checkout", "Users report a problem during payment"))
    assert candidate is not None
    assert candidate.matched_terms["affected_feature_match_kind"] == "exact"


def test_substring_affected_feature_match() -> None:
    candidate = match_issue(_decision(["checkout"]), _issue("checkout flow", "Users report a problem during payment"))
    assert candidate is not None
    assert candidate.matched_terms["affected_feature_match_kind"] == "substring"
    assert candidate.score == 0.7


def test_title_keyword_match_without_affected_feature_match() -> None:
    candidate = match_issue(_decision(["checkout"]), _issue("billing", "Users cannot complete checkout"))
    assert candidate is not None
    assert candidate.method == MatchMethod.TITLE
    assert candidate.matched_terms["affected_feature_matched_keywords"] == []
    assert candidate.score == 0.5


def test_multiple_keywords_partial_title_overlap() -> None:
    candidate = match_issue(
        _decision(["checkout", "unrelated_term"]), _issue("billing", "Users cannot complete checkout")
    )
    assert candidate is not None
    assert candidate.score == 0.25  # 1/2 overlap * 0.5 weight


def test_category_alone_never_produces_a_match() -> None:
    candidate = match_issue(
        _decision(["zzz_no_such_keyword_zzz"], category=FeedbackCategory.BUG),
        _issue("checkout", "Something else entirely", category=FeedbackCategory.BUG),
    )
    assert candidate is None


def test_category_boosts_an_existing_match_but_is_visible_and_documented() -> None:
    without_category = match_issue(
        _decision(["checkout"]), _issue("billing", "Users cannot complete checkout", category=FeedbackCategory.BUG)
    )
    with_category = match_issue(
        _decision(["checkout"], category=FeedbackCategory.BUG),
        _issue("billing", "Users cannot complete checkout", category=FeedbackCategory.BUG),
    )
    assert without_category.score == 0.5
    assert with_category.score == 0.65
    assert with_category.matched_terms["category_matched"] is True


# ============================ Integration: end-to-end ============================


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


def _create_decision(db_session, keywords=None, category=None, query_text="Should we prioritize fixing checkout?") -> Decision:
    decision = Decision(query_text=query_text, keywords=keywords or ["checkout"], category=category)
    db_session.add(decision)
    db_session.commit()
    db_session.refresh(decision)
    return decision


def _insert_analysis(db_session, feedback_id: str, severity: int) -> None:
    from app.db.models.feedback_analysis import FeedbackAnalysis

    db_session.add(
        FeedbackAnalysis(
            feedback_id=uuid.UUID(feedback_id),
            category=FeedbackCategory.BUG,
            affected_feature="checkout",
            severity=severity,
            confidence=0.9,
            summary="Checkout is broken.",
            provider="gemini",
            model="gemini-3.8-flash",
        )
    )
    db_session.commit()


def test_end_to_end_strong_evidence_fires_all_supporting_rules(client, db_session) -> None:
    ids = []
    sources = ["app_store", "app_store", "app_store", "zendesk", "zendesk"]
    customers = ["cust-1", "cust-1", "cust-2", None, None]
    tiers = ["enterprise", "enterprise", "free", None, None]
    recent = (REFERENCE_TIME - timedelta(days=1)).isoformat()
    for i in range(5):
        fb = _create_feedback(
            client,
            external_id=f"strong-{i}",
            source=sources[i],
            customer_id=customers[i],
            customer_tier=tiers[i],
            created_at=recent,
        )
        _insert_analysis(db_session, fb["id"], severity=8)
        ids.append(fb["id"])

    issue = _create_issue(db_session, ids)
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    decision = _create_decision(db_session)

    evaluation = evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)
    evidence = db_session.execute(
        select(DecisionIssueEvidence).where(DecisionIssueEvidence.decision_evaluation_id == evaluation.id)
    ).scalar_one()

    supporting_codes = _codes(evidence.supporting)
    assert supporting_codes == {
        "high_frequency",
        "high_severity_concentration",
        "increasing_trend",
        "multiple_customer_segments",
        "multiple_independent_sources",
        "broad_known_customer_reach",
        "sustained_recency",
    }
    assert evidence.counter == []
    assert evidence.gaps == []
    assert evidence.match_method == MatchMethod.AFFECTED_FEATURE_AND_TITLE
    assert evidence.product_issue_signal_snapshot_id is not None


def test_end_to_end_weak_evidence_is_gaps_not_counter(client, db_session) -> None:
    """Direct test of the CRITICAL RULE: 2 feedback items from one source is
    insufficient evidence to judge source diversity -> GAP, never COUNTER."""
    ids = []
    for i in range(2):
        fb = _create_feedback(client, external_id=f"weak-{i}", source="app_store")
        ids.append(fb["id"])

    issue = _create_issue(db_session, ids)
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    decision = _create_decision(db_session)

    evaluation = evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)
    evidence = db_session.execute(
        select(DecisionIssueEvidence).where(DecisionIssueEvidence.decision_evaluation_id == evaluation.id)
    ).scalar_one()

    assert "single_source_concentration" not in _codes(evidence.counter)
    gap_codes = _codes(evidence.gaps)
    assert "insufficient_source_diversity_data" in gap_codes
    assert "low_volume_overall" in gap_codes


def test_end_to_end_high_volume_single_source_is_counter(client, db_session) -> None:
    """Direct test of the CRITICAL RULE: 10+ feedback items from one source
    is enough volume to observe concentration -> COUNTER."""
    ids = []
    for i in range(10):
        fb = _create_feedback(client, external_id=f"concentrated-{i}", source="app_store")
        ids.append(fb["id"])

    issue = _create_issue(db_session, ids)
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    decision = _create_decision(db_session)

    evaluation = evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)
    evidence = db_session.execute(
        select(DecisionIssueEvidence).where(DecisionIssueEvidence.decision_evaluation_id == evaluation.id)
    ).scalar_one()

    assert "single_source_concentration" in _codes(evidence.counter)
    assert "insufficient_source_diversity_data" not in _codes(evidence.gaps)


def test_no_matching_issues_creates_decision_level_gap(db_session) -> None:
    decision = _create_decision(db_session, keywords=["nonexistent_feature_xyz"])

    evaluation = evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    assert _codes(evaluation.decision_level_gaps) == {"no_matching_issues_found"}
    evidence_count = db_session.execute(
        select(DecisionIssueEvidence).where(DecisionIssueEvidence.decision_evaluation_id == evaluation.id)
    ).scalars().all()
    assert evidence_count == []


def test_matched_issue_with_no_signal_snapshot_yields_no_signal_snapshot_gap(client, db_session) -> None:
    fb = _create_feedback(client, external_id="nosig")
    issue = _create_issue(db_session, [fb["id"]])
    # Deliberately never call calculate_issue_signals for this issue.
    decision = _create_decision(db_session)

    evaluation = evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)
    evidence = db_session.execute(
        select(DecisionIssueEvidence).where(DecisionIssueEvidence.decision_evaluation_id == evaluation.id)
    ).scalar_one()

    assert _codes(evidence.gaps) == {"no_signal_snapshot"}
    assert evidence.supporting == []
    assert evidence.counter == []
    assert evidence.product_issue_signal_snapshot_id is None


def test_find_matching_issues_excludes_weak_matches(client, db_session) -> None:
    strong_fb = _create_feedback(client, external_id="strong-match")
    strong_issue = _create_issue(db_session, [strong_fb["id"]], affected_feature="checkout", title="Checkout is broken")

    weak_fb = _create_feedback(client, external_id="weak-match")
    weak_issue = _create_issue(
        db_session, [weak_fb["id"]], affected_feature="billing", title="An unrelated report mentioning checkout only in passing"
    )

    decision = _create_decision(db_session, keywords=["checkout", "alpha", "beta", "gamma"])

    candidates = find_matching_issues(db_session, decision)
    matched_ids = {c.issue.id for c in candidates}

    assert strong_issue.id in matched_ids
    assert weak_issue.id not in matched_ids
    for candidate in candidates:
        assert candidate.score >= MIN_MATCH_SCORE


# ============================ Provenance ============================


def test_recorded_facts_survive_signal_recalculation(client, db_session) -> None:
    ids = []
    for i in range(6):
        fb = _create_feedback(client, external_id=f"prov-{i}", source="app_store")
        ids.append(fb["id"])
    issue = _create_issue(db_session, ids)
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    decision = _create_decision(db_session)

    evaluation = evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)
    evidence_id = db_session.execute(
        select(DecisionIssueEvidence.id).where(DecisionIssueEvidence.decision_evaluation_id == evaluation.id)
    ).scalar_one()
    original_supporting = db_session.get(DecisionIssueEvidence, evidence_id).supporting
    original_feedback_count = next(f for f in original_supporting if f["code"] == "high_frequency")["value"]["feedback_count"]
    assert original_feedback_count == 6

    # Grow the issue's membership and recalculate signals — the live
    # snapshot now reflects 10 items, not 6.
    extra_ids = []
    for i in range(4):
        fb = _create_feedback(client, external_id=f"prov-extra-{i}", source="app_store")
        db_session.add(ProductIssueFeedback(product_issue_id=issue.id, feedback_id=uuid.UUID(fb["id"])))
        extra_ids.append(fb["id"])
    db_session.commit()
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    # The historical evidence row must be untouched.
    unchanged = db_session.get(DecisionIssueEvidence, evidence_id)
    unchanged_feedback_count = next(f for f in unchanged.supporting if f["code"] == "high_frequency")["value"]["feedback_count"]
    assert unchanged_feedback_count == 6
    assert unchanged.supporting == original_supporting


def test_deleting_signal_snapshot_nulls_fk_without_destroying_evidence(client, db_session) -> None:
    fb = _create_feedback(client, external_id="del-snap", source="app_store")
    issue = _create_issue(db_session, [fb["id"]])
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    decision = _create_decision(db_session)

    evaluation = evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)
    evidence_id = db_session.execute(
        select(DecisionIssueEvidence.id).where(DecisionIssueEvidence.decision_evaluation_id == evaluation.id)
    ).scalar_one()
    before = db_session.get(DecisionIssueEvidence, evidence_id)
    assert before.product_issue_signal_snapshot_id is not None
    original_gaps = before.gaps

    db_session.execute(delete(ProductIssueSignalSnapshot).where(ProductIssueSignalSnapshot.product_issue_id == issue.id))
    db_session.commit()

    after = db_session.get(DecisionIssueEvidence, evidence_id)
    assert after.product_issue_signal_snapshot_id is None
    assert after.gaps == original_gaps  # evidence content untouched


def test_evidence_facts_include_threshold_and_source_signal_path() -> None:
    snapshot = _fake_snapshot(feedback_count=HIGH_FREQUENCY_THRESHOLD)
    fact = next(f for f in _supporting_facts(snapshot) if f["code"] == "high_frequency")
    assert fact["threshold"] == HIGH_FREQUENCY_THRESHOLD
    assert fact["source_signal_path"] == "feedback_count"
    assert "value" in fact and "label" in fact


# ============================ Append-only behavior ============================


def test_evaluate_is_append_only_and_deterministic(client, db_session) -> None:
    """Deliberately the opposite assertion from Phase 3C's single-row
    determinism test: DecisionEvaluation is append-only by design (needed
    for future decision-stability history), so two evaluations must produce
    TWO rows, not one. Do not "fix" this to assert a single row."""
    fb = _create_feedback(client, external_id="append-only", source="app_store")
    issue = _create_issue(db_session, [fb["id"]])
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    decision = _create_decision(db_session)

    first = evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)
    second = evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    assert first.id != second.id

    all_evaluations = db_session.execute(
        select(DecisionEvaluation).where(DecisionEvaluation.decision_id == decision.id)
    ).scalars().all()
    assert len(all_evaluations) == 2

    first_evidence = db_session.execute(
        select(DecisionIssueEvidence).where(DecisionIssueEvidence.decision_evaluation_id == first.id)
    ).scalar_one()
    second_evidence = db_session.execute(
        select(DecisionIssueEvidence).where(DecisionIssueEvidence.decision_evaluation_id == second.id)
    ).scalar_one()

    assert first_evidence.supporting == second_evidence.supporting
    assert first_evidence.counter == second_evidence.counter
    assert first_evidence.gaps == second_evidence.gaps
    assert first_evidence.match_score == second_evidence.match_score

    # First evaluation's row is still intact after the second call.
    reloaded_first = db_session.get(DecisionEvaluation, first.id)
    assert reloaded_first is not None
    assert reloaded_first.evaluation_version == DECISION_EVALUATION_VERSION


def test_list_evaluations_and_get_latest(client, db_session) -> None:
    fb = _create_feedback(client, external_id="hist", source="app_store")
    issue = _create_issue(db_session, [fb["id"]])
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    decision = _create_decision(db_session)

    assert get_latest_evaluation(db_session, decision.id) is None

    first = evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)
    second = evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME + timedelta(minutes=1))

    items, total = list_evaluations(db_session, decision.id, limit=10, offset=0)
    assert total == 2
    assert {e.id for e in items} == {first.id, second.id}

    latest = get_latest_evaluation(db_session, decision.id)
    assert latest.id == second.id
    assert get_evaluation(db_session, first.id).id == first.id


# ================================= API ============================


def _payload(**overrides) -> dict:
    payload = {"query_text": "Should we prioritize fixing checkout?", "keywords": ["checkout"]}
    payload.update(overrides)
    return payload


def test_create_decision_via_api(client) -> None:
    response = client.post("/api/v1/decisions", json=_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["query_text"] == "Should we prioritize fixing checkout?"
    assert body["keywords"] == ["checkout"]
    assert body["status"] == "open"
    uuid.UUID(body["id"])


def test_create_decision_blank_query_text_fails_validation(client) -> None:
    response = client.post("/api/v1/decisions", json=_payload(query_text="   "))
    assert response.status_code == 422


def test_create_decision_empty_keywords_fails_validation(client) -> None:
    response = client.post("/api/v1/decisions", json=_payload(keywords=[]))
    assert response.status_code == 422


def test_get_decision_not_found(client) -> None:
    response = client.get(f"/api/v1/decisions/{uuid.uuid4()}")
    assert response.status_code == 404


def test_list_decisions_pagination(client) -> None:
    for i in range(3):
        client.post("/api/v1/decisions", json=_payload(query_text=f"Question {i}", keywords=[f"kw{i}"]))

    page = client.get("/api/v1/decisions", params={"limit": 2, "offset": 0}).json()
    assert page["total"] == 3
    assert len(page["items"]) == 2


def test_evaluate_endpoint_not_found_for_missing_decision(client) -> None:
    response = client.post(f"/api/v1/decisions/{uuid.uuid4()}/evaluate")
    assert response.status_code == 404


def test_evaluate_endpoint_end_to_end(client, db_session) -> None:
    fb = _create_feedback(client, external_id="api-eval", source="app_store")
    issue = _create_issue(db_session, [fb["id"]])
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    create_response = client.post("/api/v1/decisions", json=_payload())
    decision_id = create_response.json()["id"]

    response = client.post(f"/api/v1/decisions/{decision_id}/evaluate")

    assert response.status_code == 200
    body = response.json()
    assert body["evaluation_version"] == DECISION_EVALUATION_VERSION
    assert len(body["issues"]) == 1
    issue_evidence = body["issues"][0]
    assert issue_evidence["product_issue_id"] == str(issue.id)
    assert "match_method" in issue_evidence
    assert "match_score" in issue_evidence
    assert "matched_terms" in issue_evidence


def test_evaluations_list_and_get_endpoints(client, db_session) -> None:
    fb = _create_feedback(client, external_id="api-hist", source="app_store")
    issue = _create_issue(db_session, [fb["id"]])
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    create_response = client.post("/api/v1/decisions", json=_payload())
    decision_id = create_response.json()["id"]

    client.post(f"/api/v1/decisions/{decision_id}/evaluate")
    second = client.post(f"/api/v1/decisions/{decision_id}/evaluate").json()

    list_response = client.get(f"/api/v1/decisions/{decision_id}/evaluations")
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 2
    assert list_response.json()["items"][0]["matched_issue_count"] == 1

    get_response = client.get(f"/api/v1/decisions/{decision_id}/evaluations/{second['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["id"] == second["id"]

    missing_eval_response = client.get(f"/api/v1/decisions/{decision_id}/evaluations/{uuid.uuid4()}")
    assert missing_eval_response.status_code == 404


def test_latest_evaluation_endpoint(client, db_session) -> None:
    fb = _create_feedback(client, external_id="api-latest", source="app_store")
    issue = _create_issue(db_session, [fb["id"]])
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    create_response = client.post("/api/v1/decisions", json=_payload())
    decision_id = create_response.json()["id"]

    not_found_response = client.get(f"/api/v1/decisions/{decision_id}/evaluations/latest")
    assert not_found_response.status_code == 404

    client.post(f"/api/v1/decisions/{decision_id}/evaluate")
    second = client.post(f"/api/v1/decisions/{decision_id}/evaluate").json()

    latest_response = client.get(f"/api/v1/decisions/{decision_id}/evaluations/latest")
    assert latest_response.status_code == 200
    assert latest_response.json()["id"] == second["id"]


def test_openapi_routes_registered(client) -> None:
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/api/v1/decisions" in paths
    assert "/api/v1/decisions/{decision_id}" in paths
    assert "/api/v1/decisions/{decision_id}/evaluate" in paths
    assert "/api/v1/decisions/{decision_id}/evaluations" in paths
    assert "/api/v1/decisions/{decision_id}/evaluations/latest" in paths
    assert "/api/v1/decisions/{decision_id}/evaluations/{evaluation_id}" in paths

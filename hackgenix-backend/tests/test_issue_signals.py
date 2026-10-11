import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models.enums import FeedbackCategory, RelationshipType
from app.db.models.feedback_analysis import FeedbackAnalysis
from app.db.models.feedback_relationship import FeedbackRelationship
from app.db.models.product_issue import ProductIssue
from app.db.models.product_issue_feedback import ProductIssueFeedback
from app.db.models.product_issue_signal_snapshot import ProductIssueSignalSnapshot
from app.services.issue_signals import (
    SIGNAL_ALGORITHM_VERSION,
    calculate_all_issue_signals,
    calculate_issue_signals,
    get_signals,
)

REFERENCE_TIME = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def _create_feedback(client, **overrides) -> dict:
    payload = {"source": "app_store", "raw_text": "The app crashes on upload."}
    payload.update(overrides)
    return client.post("/api/v1/feedback", json=payload).json()


def _create_issue(db_session, feedback_ids: list[str], title: str = "Test issue") -> ProductIssue:
    issue = ProductIssue(
        canonical_feedback_id=uuid.UUID(feedback_ids[0]) if feedback_ids else uuid.uuid4(),
        title=title,
        clustering_version="cluster-v1",
    )
    db_session.add(issue)
    db_session.flush()
    for fid in feedback_ids:
        db_session.add(ProductIssueFeedback(product_issue_id=issue.id, feedback_id=uuid.UUID(fid)))
    db_session.commit()
    db_session.refresh(issue)
    return issue


def _create_issue_no_members(client, db_session) -> ProductIssue:
    """An issue whose canonical feedback isn't (or is no longer) linked via
    product_issue_feedback — exercises the zero-evidence path (e.g. the
    cohesion filter peeled the canonical member off in a later rerun; see
    ProductIssue's docstring)."""
    anchor = _create_feedback(client, external_id="anchor-only")
    issue = ProductIssue(
        canonical_feedback_id=uuid.UUID(anchor["id"]), title="Empty issue", clustering_version="cluster-v1"
    )
    db_session.add(issue)
    db_session.commit()
    db_session.refresh(issue)
    return issue


def _insert_analysis(
    db_session,
    feedback_id: str,
    severity: int,
    category: FeedbackCategory = FeedbackCategory.BUG,
    affected_feature: str = "upload",
    confidence: float = 0.9,
) -> FeedbackAnalysis:
    analysis = FeedbackAnalysis(
        feedback_id=uuid.UUID(feedback_id),
        category=category,
        affected_feature=affected_feature,
        severity=severity,
        confidence=confidence,
        summary="A representative summary.",
        provider="gemini",
        model="gemini-3.8-flash",
    )
    db_session.add(analysis)
    db_session.commit()
    return analysis


def _insert_relationship(
    db_session, id_a: str, id_b: str, relationship_type: RelationshipType, similarity: float = 0.95
) -> FeedbackRelationship:
    a, b = uuid.UUID(id_a), uuid.UUID(id_b)
    lo, hi = (a, b) if a < b else (b, a)
    rel = FeedbackRelationship(
        feedback_id_a=lo,
        feedback_id_b=hi,
        relationship_type=relationship_type,
        similarity_score=similarity,
        algorithm_version="semantic-v1",
    )
    db_session.add(rel)
    db_session.commit()
    return rel


# --- Frequency / empty / single / multiple item evidence ----------------------


def test_signals_for_issue_with_no_members_are_all_zero_or_insufficient(client, db_session) -> None:
    issue = _create_issue_no_members(client, db_session)

    snapshot = calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    assert snapshot.feedback_count == 0
    assert snapshot.severity["analyzed_count"] == 0
    assert snapshot.severity["mean"] is None
    assert snapshot.recency["latest_feedback_at"] is None
    assert snapshot.trend["sufficient_data"] is False
    assert snapshot.trend["direction"] == "insufficient_data"
    assert snapshot.duplicate_ratio["duplicate_ratio"] is None
    assert snapshot.duplicate_ratio["possible_pairs"] == 0
    assert snapshot.evidence_quality["customer_reach_known"] is False
    assert snapshot.evidence_quality["recency"] == "unknown"


def test_signals_for_single_feedback_item(client, db_session) -> None:
    a = _create_feedback(client, external_id="a", customer_id="cust-1")
    issue = _create_issue(db_session, [a["id"]])

    snapshot = calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    assert snapshot.feedback_count == 1
    assert snapshot.customer_reach["distinct_known_customers"] == 1
    # a single item has no possible relationship pair with itself
    assert snapshot.duplicate_ratio["possible_pairs"] == 0
    assert snapshot.duplicate_ratio["duplicate_ratio"] is None


def test_signals_for_multiple_feedback_items(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    c = _create_feedback(client, external_id="c")
    issue = _create_issue(db_session, [a["id"], b["id"], c["id"]])

    snapshot = calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    assert snapshot.feedback_count == 3
    assert snapshot.duplicate_ratio["possible_pairs"] == 3  # C(3, 2)


# --- Severity distribution ------------------------------------------------------


def test_severity_distribution_preserves_full_histogram_and_buckets(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    c = _create_feedback(client, external_id="c")
    _insert_analysis(db_session, a["id"], severity=2)  # low
    _insert_analysis(db_session, b["id"], severity=2)  # low
    _insert_analysis(db_session, c["id"], severity=9)  # critical
    issue = _create_issue(db_session, [a["id"], b["id"], c["id"]])

    snapshot = calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    assert snapshot.severity["distribution"] == {"2": 2, "9": 1}
    assert snapshot.severity["buckets"] == {"low": 2, "medium": 0, "high": 0, "critical": 1}
    assert snapshot.severity["analyzed_count"] == 3
    assert snapshot.severity["unanalyzed_count"] == 0
    assert snapshot.severity["mean"] == pytest.approx((2 + 2 + 9) / 3, abs=0.01)
    assert snapshot.severity["median"] == 2.0


def test_severity_distribution_with_missing_analysis(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    _insert_analysis(db_session, a["id"], severity=5)
    # b has no FeedbackAnalysis row at all
    issue = _create_issue(db_session, [a["id"], b["id"]])

    snapshot = calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    assert snapshot.severity["analyzed_count"] == 1
    assert snapshot.severity["unanalyzed_count"] == 1
    assert snapshot.evidence_quality["severity_data_available"] is True
    assert snapshot.evidence_quality["severity_analyzed_fraction"] == 0.5


# --- Customer reach: known / anonymous / tiers ----------------------------------


def test_customer_reach_distinguishes_known_from_anonymous(client, db_session) -> None:
    a = _create_feedback(client, external_id="a", customer_id="cust-1")
    b = _create_feedback(client, external_id="b", customer_id="cust-1")  # same customer, 2nd item
    c = _create_feedback(client, external_id="c")  # no customer_id at all -> anonymous
    issue = _create_issue(db_session, [a["id"], b["id"], c["id"]])

    snapshot = calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    assert snapshot.customer_reach["distinct_known_customers"] == 1
    assert snapshot.customer_reach["known_feedback_count"] == 2
    assert snapshot.customer_reach["anonymous_feedback_count"] == 1


def test_missing_customer_ids_all_anonymous(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    issue = _create_issue(db_session, [a["id"], b["id"]])

    snapshot = calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    assert snapshot.customer_reach["distinct_known_customers"] == 0
    assert snapshot.customer_reach["anonymous_feedback_count"] == 2
    assert snapshot.evidence_quality["customer_reach_known"] is False


def test_customer_tiers_are_distributed_not_invented(client, db_session) -> None:
    a = _create_feedback(client, external_id="a", customer_tier="enterprise")
    b = _create_feedback(client, external_id="b", customer_tier="enterprise")
    c = _create_feedback(client, external_id="c", customer_tier="free")
    d = _create_feedback(client, external_id="d")  # no tier
    issue = _create_issue(db_session, [a["id"], b["id"], c["id"], d["id"]])

    snapshot = calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    assert snapshot.segment_coverage["customer_tier_distribution"] == {"enterprise": 2, "free": 1}
    assert snapshot.segment_coverage["unknown_tier_count"] == 1
    assert snapshot.evidence_quality["segment_coverage_known"] is True


# --- Source diversity -------------------------------------------------------------


def test_source_diversity_preserves_distribution(client, db_session) -> None:
    a = _create_feedback(client, external_id="a", source="app_store")
    b = _create_feedback(client, external_id="b", source="app_store")
    c = _create_feedback(client, external_id="c", source="zendesk")
    issue = _create_issue(db_session, [a["id"], b["id"], c["id"]])

    snapshot = calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    assert snapshot.source_diversity["distinct_sources"] == 2
    assert snapshot.source_diversity["source_distribution"] == {"app_store": 2, "zendesk": 1}
    assert snapshot.evidence_quality["source_diversity_sufficient"] is True


def test_single_source_is_insufficient_for_evidence_quality(client, db_session) -> None:
    a = _create_feedback(client, external_id="a", source="app_store")
    b = _create_feedback(client, external_id="b", source="app_store")
    issue = _create_issue(db_session, [a["id"], b["id"]])

    snapshot = calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    assert snapshot.source_diversity["distinct_sources"] == 1
    assert snapshot.evidence_quality["source_diversity_sufficient"] is False


# --- Duplicate ratio ---------------------------------------------------------------


def test_duplicate_ratio_counts_only_internal_pairs(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    c = _create_feedback(client, external_id="c")
    outsider = _create_feedback(client, external_id="outsider")
    _insert_relationship(db_session, a["id"], b["id"], RelationshipType.DUPLICATE)
    _insert_relationship(db_session, b["id"], c["id"], RelationshipType.RELATED)
    # relationship touching a non-member must not be counted
    _insert_relationship(db_session, c["id"], outsider["id"], RelationshipType.DUPLICATE)
    issue = _create_issue(db_session, [a["id"], b["id"], c["id"]])

    snapshot = calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    assert snapshot.duplicate_ratio["duplicate_pair_count"] == 1
    assert snapshot.duplicate_ratio["related_pair_count"] == 1
    assert snapshot.duplicate_ratio["total_relationship_pairs"] == 2
    assert snapshot.duplicate_ratio["possible_pairs"] == 3
    assert snapshot.duplicate_ratio["duplicate_ratio"] == 0.5
    assert snapshot.evidence_quality["duplicate_concentration"] == "moderate"


def test_related_only_relationships_yield_zero_duplicate_ratio(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    _insert_relationship(db_session, a["id"], b["id"], RelationshipType.RELATED)
    issue = _create_issue(db_session, [a["id"], b["id"]])

    snapshot = calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    assert snapshot.duplicate_ratio["duplicate_pair_count"] == 0
    assert snapshot.duplicate_ratio["related_pair_count"] == 1
    assert snapshot.duplicate_ratio["duplicate_ratio"] == 0.0
    assert snapshot.evidence_quality["duplicate_concentration"] == "low"


def test_duplicate_ratio_is_insufficient_data_with_no_relationships(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    issue = _create_issue(db_session, [a["id"], b["id"]])

    snapshot = calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    assert snapshot.duplicate_ratio["duplicate_ratio"] is None
    assert snapshot.evidence_quality["duplicate_concentration"] == "insufficient_data"


# --- Recency -----------------------------------------------------------------------


def test_recency_reports_latest_and_oldest_relative_to_reference(client, db_session) -> None:
    oldest_at = REFERENCE_TIME - timedelta(days=40)
    latest_at = REFERENCE_TIME - timedelta(days=5)
    a = _create_feedback(client, external_id="a", created_at=oldest_at.isoformat())
    b = _create_feedback(client, external_id="b", created_at=latest_at.isoformat())
    issue = _create_issue(db_session, [a["id"], b["id"]])

    snapshot = calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    assert snapshot.recency["days_since_oldest"] == 40
    assert snapshot.recency["days_since_latest"] == 5
    assert snapshot.evidence_quality["recency"] == "recent"  # 5 days <= RECENCY_STALE_DAYS


def test_recency_flags_stale_when_latest_is_old(client, db_session) -> None:
    old_at = REFERENCE_TIME - timedelta(days=90)
    a = _create_feedback(client, external_id="a", created_at=old_at.isoformat())
    issue = _create_issue(db_session, [a["id"]])

    snapshot = calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    assert snapshot.recency["days_since_latest"] == 90
    assert snapshot.evidence_quality["recency"] == "stale"


# --- Trend ---------------------------------------------------------------------------


def test_trend_with_sufficient_data_detects_increase(client, db_session) -> None:
    current_dates = [REFERENCE_TIME - timedelta(days=1), REFERENCE_TIME - timedelta(days=2), REFERENCE_TIME - timedelta(days=3)]
    previous_dates = [REFERENCE_TIME - timedelta(days=10)]
    ids = []
    for i, dt in enumerate(current_dates + previous_dates):
        fb = _create_feedback(client, external_id=f"trend-up-{i}", created_at=dt.isoformat())
        ids.append(fb["id"])
    issue = _create_issue(db_session, ids)

    snapshot = calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    assert snapshot.trend["current_window_count"] == 3
    assert snapshot.trend["previous_window_count"] == 1
    assert snapshot.trend["sufficient_data"] is True
    assert snapshot.trend["direction"] == "increasing"


def test_trend_with_insufficient_data_is_explicit(client, db_session) -> None:
    a = _create_feedback(client, external_id="a", created_at=(REFERENCE_TIME - timedelta(days=1)).isoformat())
    issue = _create_issue(db_session, [a["id"]])

    snapshot = calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    assert snapshot.trend["sufficient_data"] is False
    assert snapshot.trend["direction"] == "insufficient_data"


# --- Time distribution -----------------------------------------------------------------


def test_time_distribution_buckets_by_week(client, db_session) -> None:
    week1_a = datetime(2026, 6, 1, tzinfo=timezone.utc)  # same ISO week
    week1_b = datetime(2026, 6, 2, tzinfo=timezone.utc)
    week2 = datetime(2026, 6, 10, tzinfo=timezone.utc)  # different week
    a = _create_feedback(client, external_id="a", created_at=week1_a.isoformat())
    b = _create_feedback(client, external_id="b", created_at=week1_b.isoformat())
    c = _create_feedback(client, external_id="c", created_at=week2.isoformat())
    issue = _create_issue(db_session, [a["id"], b["id"], c["id"]])

    snapshot = calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    assert snapshot.time_distribution["granularity"] == "week"
    counts = {b["period_start"]: b["count"] for b in snapshot.time_distribution["buckets"]}
    week1_start = (week1_a - timedelta(days=week1_a.weekday())).date().isoformat()
    week2_start = (week2 - timedelta(days=week2.weekday())).date().isoformat()
    assert counts[week1_start] == 2
    assert counts[week2_start] == 1


# --- Deterministic reruns / one-issue / all-issues calculation --------------------------


def test_recalculating_signals_is_deterministic_and_upserts_single_row(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    _insert_analysis(db_session, a["id"], severity=4)
    issue = _create_issue(db_session, [a["id"], b["id"]])

    first = calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    second = calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    assert first.id == second.id  # same row, not a new one
    assert first.severity == second.severity
    assert first.duplicate_ratio == second.duplicate_ratio

    rows = db_session.execute(
        select(ProductIssueSignalSnapshot).where(ProductIssueSignalSnapshot.product_issue_id == issue.id)
    ).scalars().all()
    assert len(rows) == 1


def test_calculate_all_issue_signals_covers_every_issue(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    c = _create_feedback(client, external_id="c")
    issue1 = _create_issue(db_session, [a["id"], b["id"]], title="Issue 1")
    issue2 = _create_issue(db_session, [c["id"]], title="Issue 2")

    snapshots = calculate_all_issue_signals(db_session, reference_time=REFERENCE_TIME)

    covered_issue_ids = {s.product_issue_id for s in snapshots}
    assert issue1.id in covered_issue_ids
    assert issue2.id in covered_issue_ids
    assert get_signals(db_session, issue1.id) is not None
    assert get_signals(db_session, issue2.id) is not None


# --- API: one-issue recalc/get, bulk recalc, validation/error handling ------------------


def test_recalculate_and_get_signals_via_api(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    issue = _create_issue(db_session, [a["id"], b["id"]])

    recalc_response = client.post(f"/api/v1/issues/{issue.id}/signals/recalculate")
    assert recalc_response.status_code == 200
    body = recalc_response.json()
    assert body["signal_version"] == SIGNAL_ALGORITHM_VERSION
    assert body["feedback_count"] == 2

    get_response = client.get(f"/api/v1/issues/{issue.id}/signals")
    assert get_response.status_code == 200
    assert get_response.json()["feedback_count"] == 2


def test_get_signals_before_recalculation_returns_signals_missing(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    issue = _create_issue(db_session, [a["id"]])

    response = client.get(f"/api/v1/issues/{issue.id}/signals")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "signals_missing"


def test_signals_endpoints_404_for_nonexistent_issue(client) -> None:
    missing_id = uuid.uuid4()

    assert client.get(f"/api/v1/issues/{missing_id}/signals").status_code == 404
    assert client.post(f"/api/v1/issues/{missing_id}/signals/recalculate").status_code == 404


def test_bulk_recalculate_endpoint_processes_all_issues(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    _create_issue(db_session, [a["id"]], title="Issue A")
    _create_issue(db_session, [b["id"]], title="Issue B")

    response = client.post("/api/v1/issues/signals/recalculate")

    assert response.status_code == 200
    body = response.json()
    assert body["signal_version"] == SIGNAL_ALGORITHM_VERSION
    assert body["issues_processed"] == 2


# --- Database constraints ------------------------------------------------------------------


def test_product_issue_id_unique_constraint_is_enforced(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    issue = _create_issue(db_session, [a["id"]])
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    duplicate_row = ProductIssueSignalSnapshot(
        product_issue_id=issue.id,
        signal_version=SIGNAL_ALGORITHM_VERSION,
        reference_at=REFERENCE_TIME,
        feedback_count=0,
        severity={},
        recency={},
        trend={},
        customer_reach={},
        source_diversity={},
        segment_coverage={},
        duplicate_ratio={},
        time_distribution={},
        evidence_quality={},
    )
    db_session.add(duplicate_row)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

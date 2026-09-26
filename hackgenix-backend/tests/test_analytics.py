import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete

from app.api import dependencies
from app.db.models.enums import FeedbackCategory, ProductIssueStatus
from app.db.models.feedback import Feedback
from app.db.models.feedback_analysis import FeedbackAnalysis
from app.db.models.product_issue import ProductIssue
from app.db.models.product_issue_feedback import ProductIssueFeedback
from app.main import app as fastapi_app
from app.services import analytics_insight
from app.services.issue_signals import calculate_issue_signals
from tests.fakes import FakeLLMProvider

REFERENCE_TIME = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def _clear_all_feedback(db_session) -> None:
    """This dev database carries real seed/demo data (used by the running
    frontend), so analytics tests can't assume a naturally empty table.
    Deleting here only affects this test's own rolled-back transaction (see
    tests/conftest.py's db_session fixture) — cascades (ON DELETE CASCADE)
    take FeedbackAnalysis, ProductIssueFeedback, ProductIssue (via
    canonical_feedback_id) and ProductIssueSignalSnapshot with it, giving a
    real, deterministic empty-database starting point for this test only."""
    db_session.execute(delete(Feedback))
    db_session.commit()


def _create_feedback(client, **overrides) -> dict:
    payload = {"source": "app_store", "raw_text": "The app crashes on upload."}
    payload.update(overrides)
    return client.post("/api/v1/feedback", json=payload).json()


def _insert_analysis(
    db_session,
    feedback_id: str,
    *,
    severity: int = 5,
    category: FeedbackCategory = FeedbackCategory.BUG,
    affected_feature: str = "upload",
) -> FeedbackAnalysis:
    analysis = FeedbackAnalysis(
        feedback_id=uuid.UUID(feedback_id),
        category=category,
        affected_feature=affected_feature,
        severity=severity,
        confidence=0.9,
        summary="A representative summary.",
        provider="gemini",
        model="gemini-3.8-flash",
    )
    db_session.add(analysis)
    db_session.commit()
    return analysis


def _create_issue(
    db_session,
    feedback_ids: list[str],
    *,
    title: str = "Test issue",
    status: ProductIssueStatus = ProductIssueStatus.OPEN,
) -> ProductIssue:
    issue = ProductIssue(
        canonical_feedback_id=uuid.UUID(feedback_ids[0]) if feedback_ids else uuid.uuid4(),
        title=title,
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


def _override_llm_provider(fake: FakeLLMProvider):
    fastapi_app.dependency_overrides[dependencies.get_llm_provider] = lambda: fake


def _reset_llm_provider():
    fastapi_app.dependency_overrides[dependencies.get_llm_provider] = lambda: FakeLLMProvider()


# --- Empty database ------------------------------------------------------------


def test_overview_on_empty_database_returns_real_zeros(client, db_session) -> None:
    _clear_all_feedback(db_session)

    resp = client.get("/api/v1/analytics/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_feedback"] == 0
    assert data["analyzed_feedback"] == 0
    assert data["analysis_coverage"] is None
    assert data["average_severity"] is None
    assert data["high_severity_feedback_count"] == 0
    assert data["unique_customers"] == 0
    assert data["total_product_issues"] == 0
    assert data["issue_volume"] == []
    assert data["issue_trends"] == []
    assert data["top_issues"] == []
    assert data["recent_feedback_trend"]["sufficient_data"] is False


def test_categories_on_empty_database_lists_all_known_categories_at_zero(client, db_session) -> None:
    _clear_all_feedback(db_session)

    resp = client.get("/api/v1/analytics/categories")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_analyzed"] == 0
    categories = {item["category"] for item in data["items"]}
    assert categories == {"bug", "feature_request", "ui_friction", "performance", "billing"}
    assert all(item["count"] == 0 for item in data["items"])
    assert all(item["percentage"] is None for item in data["items"])


def test_sources_on_empty_database_returns_empty_list(client, db_session) -> None:
    _clear_all_feedback(db_session)

    resp = client.get("/api/v1/analytics/sources")
    assert resp.status_code == 200
    assert resp.json() == {"total_feedback": 0, "items": []}


def test_trends_on_empty_database_reports_insufficient_history(client, db_session) -> None:
    _clear_all_feedback(db_session)

    resp = client.get("/api/v1/analytics/trends?days=7")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["points"]) == 7
    assert all(point["feedback_count"] == 0 for point in data["points"])
    assert data["sufficient_history"] is False
    assert data["message"] == "Not enough historical feedback to establish a reliable trend."
    assert data["earliest_feedback_at"] is None


# --- Overview calculations -------------------------------------------------------


def test_overview_calculates_real_aggregate_counts(client, db_session) -> None:
    _clear_all_feedback(db_session)
    a = _create_feedback(client, external_id="a", customer_id="cust-1")
    b = _create_feedback(client, external_id="b", customer_id="cust-2")
    _create_feedback(client, external_id="c")  # left unanalyzed on purpose
    _insert_analysis(db_session, a["id"], severity=8, category=FeedbackCategory.BUG)
    _insert_analysis(db_session, b["id"], severity=3, category=FeedbackCategory.PERFORMANCE)

    data = client.get("/api/v1/analytics/overview").json()

    assert data["total_feedback"] == 3
    assert data["analyzed_feedback"] == 2
    assert data["unanalyzed_feedback"] == 1
    assert data["analysis_coverage"] == pytest.approx(2 / 3, rel=1e-3)
    assert data["high_severity_feedback_count"] == 1
    assert data["average_severity"] == pytest.approx(5.5)
    assert data["unique_customers"] == 2


def test_overview_open_and_resolved_product_issue_counts(client, db_session) -> None:
    _clear_all_feedback(db_session)
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    _create_issue(db_session, [a["id"]], title="Open one", status=ProductIssueStatus.OPEN)
    _create_issue(db_session, [b["id"]], title="Resolved one", status=ProductIssueStatus.RESOLVED)

    data = client.get("/api/v1/analytics/overview").json()

    assert data["total_product_issues"] == 2
    assert data["open_product_issues"] == 1
    assert data["resolved_product_issues"] == 1
    titles = {item["title"] for item in data["issue_volume"]}
    assert titles == {"Open one", "Resolved one"}


# --- Category calculations --------------------------------------------------------


def test_categories_returns_real_counts_and_percentages(client, db_session) -> None:
    _clear_all_feedback(db_session)
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    c = _create_feedback(client, external_id="c")
    _insert_analysis(db_session, a["id"], category=FeedbackCategory.BUG)
    _insert_analysis(db_session, b["id"], category=FeedbackCategory.BUG)
    _insert_analysis(db_session, c["id"], category=FeedbackCategory.PERFORMANCE)

    data = client.get("/api/v1/analytics/categories").json()
    by_category = {item["category"]: item for item in data["items"]}

    assert by_category["bug"]["count"] == 2
    assert by_category["bug"]["percentage"] == pytest.approx(66.67, rel=1e-2)
    assert by_category["performance"]["count"] == 1
    assert by_category["ui_friction"]["count"] == 0
    assert by_category["ui_friction"]["percentage"] == 0.0


# --- Source calculations -----------------------------------------------------------


def test_sources_returns_real_counts_and_percentages(client, db_session) -> None:
    _clear_all_feedback(db_session)
    a = _create_feedback(client, external_id="a", source="zendesk")
    b = _create_feedback(client, external_id="b", source="zendesk")
    _create_feedback(client, external_id="c", source="app_store")
    _insert_analysis(db_session, a["id"])

    data = client.get("/api/v1/analytics/sources").json()
    by_source = {item["source"]: item for item in data["items"]}

    assert by_source["zendesk"]["feedback_count"] == 2
    assert by_source["zendesk"]["analyzed_count"] == 1
    assert by_source["zendesk"]["percentage"] == pytest.approx(66.67, rel=1e-2)
    assert by_source["app_store"]["feedback_count"] == 1
    assert by_source["app_store"]["analyzed_count"] == 0


# --- Trend calculations ---------------------------------------------------------


def test_trends_returns_one_point_per_day_with_real_counts(client, db_session) -> None:
    _clear_all_feedback(db_session)
    _create_feedback(client, external_id="a")
    _create_feedback(client, external_id="b")

    data = client.get("/api/v1/analytics/trends?days=3").json()

    assert len(data["points"]) == 3
    assert data["points"][-1]["feedback_count"] == 2  # ingested "today", the last bucket
    assert data["points"][0]["feedback_count"] == 0
    assert data["days_with_data"] == 1


def test_trends_high_severity_count_matches_severity_threshold(client, db_session) -> None:
    _clear_all_feedback(db_session)
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    _insert_analysis(db_session, a["id"], severity=9)
    _insert_analysis(db_session, b["id"], severity=2)

    data = client.get("/api/v1/analytics/trends?days=1").json()

    assert data["points"][0]["feedback_count"] == 2
    assert data["points"][0]["analyzed_count"] == 2
    assert data["points"][0]["high_severity_count"] == 1


# --- Issue analytics --------------------------------------------------------------


def test_top_issues_returns_underlying_metrics_before_signals_calculated(client, db_session) -> None:
    _clear_all_feedback(db_session)
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    _create_issue(db_session, [a["id"], b["id"]], title="Checkout issue")

    data = client.get("/api/v1/analytics/issues").json()

    assert data["total"] == 1
    assert data["ranking_version"]
    assert data["ranking_method"]
    item = data["items"][0]
    assert item["title"] == "Checkout issue"
    assert item["feedback_count"] == 2
    assert item["signals_calculated"] is False
    assert item["average_severity"] is None
    assert item["trend_direction"] is None


def test_top_issues_includes_real_signal_components_once_calculated(client, db_session) -> None:
    _clear_all_feedback(db_session)
    a = _create_feedback(client, external_id="a", customer_id="cust-1")
    b = _create_feedback(client, external_id="b", customer_id="cust-2")
    _insert_analysis(db_session, a["id"], severity=9)
    _insert_analysis(db_session, b["id"], severity=7)
    issue = _create_issue(db_session, [a["id"], b["id"]], title="Checkout issue")
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)

    item = client.get("/api/v1/analytics/issues").json()["items"][0]

    assert item["signals_calculated"] is True
    assert item["average_severity"] == pytest.approx(8.0)
    assert item["high_severity_count"] == 2
    assert item["distinct_known_customers"] == 2
    assert item["trend_direction"] in {"increasing", "decreasing", "flat", "insufficient_data"}


def test_top_issues_ranking_orders_by_feedback_count_first(client, db_session) -> None:
    _clear_all_feedback(db_session)
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    c = _create_feedback(client, external_id="c")
    _create_issue(db_session, [a["id"]], title="Small issue")
    _create_issue(db_session, [b["id"], c["id"]], title="Bigger issue")

    items = client.get("/api/v1/analytics/issues").json()["items"]

    titles_in_order = [item["title"] for item in items]
    assert titles_in_order.index("Bigger issue") < titles_in_order.index("Small issue")


# --- AI Analytics Insight: success -----------------------------------------------


def test_insights_success_returns_grounded_output_with_resolved_evidence(client) -> None:
    resp = client.post("/api/v1/analytics/insights")

    assert resp.status_code == 200
    data = resp.json()
    assert data["headline"]
    assert data["provider"]
    assert data["model"]
    assert data["insight_version"]
    all_claims = (
        data["key_findings"] + data["emerging_patterns"] + data["areas_to_investigate"] + data["caveats"]
    )
    assert all_claims, "fake provider should return at least one finding"
    for claim in all_claims:
        assert claim["evidence_ids"]
        for evidence_id in claim["evidence_ids"]:
            assert evidence_id in data["evidence"]


def test_insights_works_on_empty_database(client, db_session) -> None:
    _clear_all_feedback(db_session)

    resp = client.post("/api/v1/analytics/insights")
    assert resp.status_code == 200


# --- AI Analytics Insight: provider failure never breaks deterministic analytics --


def test_insights_provider_failure_returns_clean_error_and_never_fabricates(client) -> None:
    _override_llm_provider(FakeLLMProvider(insight_raise_provider_error=True))
    try:
        resp = client.post("/api/v1/analytics/insights")
        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "llm_provider_error"

        # The deterministic analytics endpoints must be completely unaffected.
        overview_resp = client.get("/api/v1/analytics/overview")
        assert overview_resp.status_code == 200
    finally:
        _reset_llm_provider()


def test_insights_output_validation_failure_returns_clean_error(client) -> None:
    _override_llm_provider(FakeLLMProvider(insight_raise_output_error=True))
    try:
        resp = client.post("/api/v1/analytics/insights")
        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "llm_output_validation_error"
    finally:
        _reset_llm_provider()


def test_insights_rejects_ungrounded_evidence_id(client) -> None:
    _override_llm_provider(FakeLLMProvider(insight_unknown_evidence_id="analytics:made_up_fact"))
    try:
        resp = client.post("/api/v1/analytics/insights")
        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "llm_ungrounded_output"
    finally:
        _reset_llm_provider()


# --- AI grounding: no raw feedback corpus is ever sent to the LLM ------------------


def test_insight_bundle_never_contains_raw_feedback_text(client, db_session) -> None:
    a = _create_feedback(client, external_id="a", raw_text="UNIQUE_SECRET_RAW_TEXT_MARKER_12345")
    _insert_analysis(db_session, a["id"])
    _create_issue(db_session, [a["id"]], title="Marker issue")

    bundle = analytics_insight.build_synthesis_input(db_session)
    serialized = bundle.model_dump_json()

    assert "UNIQUE_SECRET_RAW_TEXT_MARKER_12345" not in serialized


def test_insight_bundle_citation_registry_matches_facts(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    _insert_analysis(db_session, a["id"])

    bundle = analytics_insight.build_synthesis_input(db_session)
    registry = bundle.citation_registry()

    assert "analytics:feedback_volume" in registry
    assert "analytics:category:bug" in registry
    assert len(registry) == len(bundle.facts)

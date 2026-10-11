import uuid
from datetime import datetime, timedelta, timezone

from app.db.models.enums import FeedbackCategory, ProductIssueStatus
from app.db.models.product_issue import ProductIssue
from app.db.models.product_issue_feedback import ProductIssueFeedback
from app.services.decision_evidence import evaluate_decision
from app.services.issue_signals import calculate_issue_signals

REFERENCE_TIME = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


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


def _create_decision(db_session, keywords=None, query_text="Should we prioritize fixing checkout?"):
    from app.db.models.decision import Decision

    decision = Decision(query_text=query_text, keywords=keywords or ["checkout"])
    db_session.add(decision)
    db_session.commit()
    db_session.refresh(decision)
    return decision


def _strong_issue(client, db_session, external_prefix="strong", affected_feature="checkout") -> ProductIssue:
    ids = []
    sources = ["app_store", "app_store", "app_store", "zendesk", "zendesk"]
    recent = (REFERENCE_TIME - timedelta(days=1)).isoformat()
    for i in range(5):
        fb = _create_feedback(client, external_id=f"{external_prefix}-{i}", source=sources[i], created_at=recent)
        ids.append(fb["id"])
    issue = _create_issue(db_session, ids, affected_feature=affected_feature)
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    return issue


def test_challenge_insufficient_history(client, db_session) -> None:
    decision = _create_decision(db_session)

    response = client.get(f"/api/v1/decisions/{decision.id}/challenge")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "insufficient_history"
    assert body["all_events"] == []
    assert body["previous_evaluation_id"] is None


def test_challenge_404_missing_decision(client) -> None:
    response = client.get(f"/api/v1/decisions/{uuid.uuid4()}/challenge")
    assert response.status_code == 404


def test_challenge_categorizes_events_and_resolves_issue_titles(client, db_session) -> None:
    fb1 = _create_feedback(client, external_id="grow-a", source="app_store")
    fb2 = _create_feedback(client, external_id="grow-b", source="app_store")
    issue = _create_issue(db_session, [fb1["id"], fb2["id"]], title="Checkout crashes on submit")
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    decision = _create_decision(db_session)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    for i in range(3):
        fb = _create_feedback(client, external_id=f"grow-extra-{i}", source="zendesk")
        db_session.add(ProductIssueFeedback(product_issue_id=issue.id, feedback_id=uuid.UUID(fb["id"])))
    db_session.commit()
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME + timedelta(minutes=1))

    response = client.get(f"/api/v1/decisions/{decision.id}/challenge")

    assert response.status_code == 200
    body = response.json()

    assert len(body["supporting_changes"]) > 0
    assert len(body["gap_changes"]) > 0
    # every event in a category bucket must also be in all_events, and
    # categories must not invent event types outside Phase 4C's vocabulary
    all_types = {e["type"] for e in body["all_events"]}
    for category in ("supporting_changes", "counter_changes", "trend_changes", "gap_changes", "relevance_changes", "value_changes", "new_issues", "disappeared_issues", "decision_level_changes"):
        for event in body[category]:
            assert event["type"] in all_types
            assert event in body["all_events"]

    # no recommendation/score/verdict field anywhere in the response
    forbidden_keys = {"recommendation", "score", "verdict", "winner", "priority_score", "best_issue"}
    assert forbidden_keys.isdisjoint(body.keys())

    # issue title resolved for display
    assert body["issue_summaries"][str(issue.id)]["title"] == "Checkout crashes on submit"


def test_challenge_reports_new_and_disappeared_issues(client, db_session) -> None:
    issue1 = _strong_issue(client, db_session, external_prefix="i1")
    decision = _create_decision(db_session)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    issue1.status = ProductIssueStatus.RESOLVED
    db_session.add(issue1)
    issue2 = _strong_issue(client, db_session, external_prefix="i2")
    db_session.commit()
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME + timedelta(minutes=1))

    response = client.get(f"/api/v1/decisions/{decision.id}/challenge")

    assert response.status_code == 200
    body = response.json()
    assert len(body["new_issues"]) == 1
    assert body["new_issues"][0]["product_issue_id"] == str(issue2.id)
    assert len(body["disappeared_issues"]) == 1
    assert body["disappeared_issues"][0]["product_issue_id"] == str(issue1.id)


def test_challenge_no_decision_level_changes_when_nothing_changed(client, db_session) -> None:
    _strong_issue(client, db_session)
    decision = _create_decision(db_session)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)
    evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)

    response = client.get(f"/api/v1/decisions/{decision.id}/challenge")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "stable"
    assert body["all_events"] == []


def test_openapi_challenge_route_registered(client) -> None:
    schema = client.get("/openapi.json").json()
    assert "/api/v1/decisions/{decision_id}/challenge" in schema["paths"]

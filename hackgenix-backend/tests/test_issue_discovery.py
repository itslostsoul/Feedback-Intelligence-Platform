import types
import uuid

from sqlalchemy import select

from app.db.models.enums import FeedbackCategory, ProcessingStatus, RelationshipType
from app.db.models.feedback_analysis import FeedbackAnalysis
from app.db.models.feedback_relationship import FeedbackRelationship
from app.db.models.product_issue import ProductIssue
from app.db.models.product_issue_feedback import ProductIssueFeedback
from app.services.issue_discovery import (
    CLUSTERING_ALGORITHM_VERSION,
    _apply_structured_cohesion_filter,
    _build_components,
    _summarize_cluster,
)


def _create_feedback(client, **overrides) -> dict:
    payload = {"source": "app_store", "raw_text": "The app crashes on upload."}
    payload.update(overrides)
    return client.post("/api/v1/feedback", json=payload).json()


def _insert_relationship(
    db_session, id_a: str, id_b: str, relationship_type: RelationshipType, similarity: float = 0.95
):
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


def _insert_analysis(
    db_session,
    feedback_id: str,
    category: FeedbackCategory,
    affected_feature: str,
    summary: str,
    severity: int = 7,
    confidence: float = 0.9,
):
    analysis = FeedbackAnalysis(
        feedback_id=uuid.UUID(feedback_id),
        category=category,
        affected_feature=affected_feature,
        severity=severity,
        confidence=confidence,
        summary=summary,
        provider="gemini",
        model="gemini-3.8-flash",
    )
    db_session.add(analysis)
    db_session.commit()
    return analysis


def _fake_analysis(feedback_id, category, affected_feature, summary="A representative summary."):
    return types.SimpleNamespace(
        feedback_id=feedback_id, category=category, affected_feature=affected_feature, summary=summary
    )


# --- pure unit tests: _build_components ---------------------------------------


def test_build_components_groups_transitively_connected_nodes() -> None:
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    components = _build_components([(a, b), (b, c)])  # d is isolated, not in edges at all

    assert len(components) == 1
    assert components[0] == {a, b, c}


def test_build_components_keeps_disconnected_pairs_separate() -> None:
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    components = _build_components([(a, b), (c, d)])

    assert {frozenset(comp) for comp in components} == {frozenset({a, b}), frozenset({c, d})}


def test_build_components_empty_edges_returns_no_components() -> None:
    assert _build_components([]) == []


# --- pure unit tests: cohesion filter + summarization -------------------------


def test_cohesion_filter_keeps_all_when_no_analyses() -> None:
    ids = {uuid.uuid4(), uuid.uuid4()}
    assert _apply_structured_cohesion_filter(ids, {}) == ids


def test_cohesion_filter_peels_off_minority_category() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    analyses = {
        a: _fake_analysis(a, FeedbackCategory.BUG, "upload"),
        b: _fake_analysis(b, FeedbackCategory.BUG, "upload"),
        c: _fake_analysis(c, FeedbackCategory.BILLING, "invoices"),
    }

    result = _apply_structured_cohesion_filter({a, b, c}, analyses)

    assert result == {a, b}


def test_cohesion_filter_keeps_unanalyzed_members() -> None:
    a, b, unanalyzed = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    analyses = {
        a: _fake_analysis(a, FeedbackCategory.BUG, "upload"),
        b: _fake_analysis(b, FeedbackCategory.BUG, "upload"),
    }

    result = _apply_structured_cohesion_filter({a, b, unanalyzed}, analyses)

    assert result == {a, b, unanalyzed}


def test_cohesion_filter_does_not_filter_without_clear_majority() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    analyses = {
        a: _fake_analysis(a, FeedbackCategory.BUG, "upload"),
        b: _fake_analysis(b, FeedbackCategory.BILLING, "invoices"),
    }

    result = _apply_structured_cohesion_filter({a, b}, analyses)

    assert result == {a, b}  # 50/50 split — no clear majority to filter against


def test_summarize_cluster_uses_analysis_summary_as_title() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    analyses = {
        a: _fake_analysis(a, FeedbackCategory.BUG, "upload", summary="Upload crashes over 5MB."),
        b: _fake_analysis(b, FeedbackCategory.BUG, "upload", summary="Photos over 5MB crash the app."),
    }

    title, category, feature = _summarize_cluster({a, b}, analyses)

    assert category == FeedbackCategory.BUG
    assert feature == "upload"
    assert title in {"Upload crashes over 5MB.", "Photos over 5MB crash the app."}


def test_summarize_cluster_without_analyses_uses_generic_title() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()

    title, category, feature = _summarize_cluster({a, b}, {})

    assert "2" in title
    assert category is None
    assert feature is None


# --- integration tests: full discovery pipeline -------------------------------


def test_discover_creates_issue_from_duplicate_pair(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    _insert_relationship(db_session, a["id"], b["id"], RelationshipType.DUPLICATE)

    response = client.post("/api/v1/issues/discover")

    assert response.status_code == 200
    body = response.json()
    assert body["components_considered"] == 1
    assert body["issues_created"] == 1
    assert body["issues_updated"] == 0
    assert body["feedback_linked"] == 2
    assert body["clustering_version"] == CLUSTERING_ALGORITHM_VERSION

    issues = db_session.execute(select(ProductIssue)).scalars().all()
    assert len(issues) == 1


def test_issue_traceability_lists_correct_feedback_ids(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    _insert_relationship(db_session, a["id"], b["id"], RelationshipType.RELATED)
    client.post("/api/v1/issues/discover")

    issue = db_session.execute(select(ProductIssue)).scalar_one()
    response = client.get(f"/api/v1/issues/{issue.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["feedback_count"] == 2
    assert set(body["feedback_ids"]) == {a["id"], b["id"]}


def test_discover_merges_transitively_connected_feedback(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    c = _create_feedback(client, external_id="c")
    _insert_relationship(db_session, a["id"], b["id"], RelationshipType.DUPLICATE)
    _insert_relationship(db_session, b["id"], c["id"], RelationshipType.RELATED)

    client.post("/api/v1/issues/discover")

    issues = db_session.execute(select(ProductIssue)).scalars().all()
    assert len(issues) == 1
    links = db_session.execute(select(ProductIssueFeedback)).scalars().all()
    assert len(links) == 3


def test_discover_keeps_disconnected_pairs_as_separate_issues(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    c = _create_feedback(client, external_id="c")
    d = _create_feedback(client, external_id="d")
    _insert_relationship(db_session, a["id"], b["id"], RelationshipType.DUPLICATE)
    _insert_relationship(db_session, c["id"], d["id"], RelationshipType.DUPLICATE)

    response = client.post("/api/v1/issues/discover")

    assert response.json()["issues_created"] == 2
    issues = db_session.execute(select(ProductIssue)).scalars().all()
    assert len(issues) == 2


def test_discover_does_not_cluster_isolated_feedback(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    isolated = _create_feedback(client, external_id="isolated")  # no relationship at all
    _insert_relationship(db_session, a["id"], b["id"], RelationshipType.DUPLICATE)

    client.post("/api/v1/issues/discover")

    links = db_session.execute(select(ProductIssueFeedback)).scalars().all()
    linked_ids = {str(link.feedback_id) for link in links}
    assert isolated["id"] not in linked_ids
    # the isolated feedback item remains fully discoverable, untouched
    assert client.get(f"/api/v1/feedback/{isolated['id']}").status_code == 200


def test_discover_applies_structured_cohesion_filter(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    c = _create_feedback(client, external_id="c")
    _insert_relationship(db_session, a["id"], b["id"], RelationshipType.DUPLICATE)
    _insert_relationship(db_session, b["id"], c["id"], RelationshipType.RELATED)
    _insert_analysis(db_session, a["id"], FeedbackCategory.BUG, "upload", "Upload crashes.")
    _insert_analysis(db_session, b["id"], FeedbackCategory.BUG, "upload", "Upload fails too.")
    _insert_analysis(db_session, c["id"], FeedbackCategory.BILLING, "invoices", "Invoice is wrong.")

    response = client.post("/api/v1/issues/discover")

    body = response.json()
    assert body["issues_created"] == 1
    assert body["feedback_linked"] == 2  # only a, b
    assert body["feedback_left_unclustered"] == 1  # c peeled off

    issue = db_session.execute(select(ProductIssue)).scalar_one()
    assert issue.category == FeedbackCategory.BUG
    assert issue.affected_feature == "upload"

    links = db_session.execute(select(ProductIssueFeedback)).scalars().all()
    linked_ids = {str(link.feedback_id) for link in links}
    assert linked_ids == {a["id"], b["id"]}
    assert c["id"] not in linked_ids


def test_discover_is_idempotent_on_rerun_with_no_new_data(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    _insert_relationship(db_session, a["id"], b["id"], RelationshipType.DUPLICATE)

    first = client.post("/api/v1/issues/discover").json()
    second = client.post("/api/v1/issues/discover").json()

    assert first["issues_created"] == 1
    assert second["issues_created"] == 0
    assert second["issues_updated"] == 1
    assert second["feedback_linked"] == 0  # no new members to link

    issues = db_session.execute(select(ProductIssue)).scalars().all()
    assert len(issues) == 1  # not duplicated


def test_discover_updates_existing_issue_when_component_grows(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    c = _create_feedback(client, external_id="c")
    _insert_relationship(db_session, a["id"], b["id"], RelationshipType.DUPLICATE)
    client.post("/api/v1/issues/discover")

    issue_before = db_session.execute(select(ProductIssue)).scalar_one()

    _insert_relationship(db_session, b["id"], c["id"], RelationshipType.RELATED)
    result = client.post("/api/v1/issues/discover").json()

    assert result["issues_created"] == 0
    assert result["issues_updated"] == 1
    assert result["feedback_linked"] == 1  # only c is new

    issue_after = db_session.execute(select(ProductIssue)).scalar_one()
    assert issue_after.id == issue_before.id  # same issue, not a new one

    links = db_session.execute(select(ProductIssueFeedback)).scalars().all()
    assert {str(link.feedback_id) for link in links} == {a["id"], b["id"], c["id"]}


def test_discover_never_deletes_feedback_or_relationships(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    _insert_relationship(db_session, a["id"], b["id"], RelationshipType.DUPLICATE)

    client.post("/api/v1/issues/discover")

    assert client.get(f"/api/v1/feedback/{a['id']}").status_code == 200
    assert client.get(f"/api/v1/feedback/{b['id']}").status_code == 200
    assert db_session.execute(select(FeedbackRelationship)).scalars().all()  # still there


def test_list_issues_pagination(client, db_session) -> None:
    for i in range(3):
        x = _create_feedback(client, external_id=f"x{i}")
        y = _create_feedback(client, external_id=f"y{i}")
        _insert_relationship(db_session, x["id"], y["id"], RelationshipType.DUPLICATE)
    client.post("/api/v1/issues/discover")

    page = client.get("/api/v1/issues", params={"limit": 2, "offset": 0}).json()

    assert page["total"] == 3
    assert len(page["items"]) == 2


def test_get_issue_not_found(client) -> None:
    response = client.get(f"/api/v1/issues/{uuid.uuid4()}")

    assert response.status_code == 404


def test_discover_with_no_relationships_creates_nothing(client) -> None:
    response = client.post("/api/v1/issues/discover")

    body = response.json()
    assert body["components_considered"] == 0
    assert body["issues_created"] == 0


def test_issue_status_defaults_to_open(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    _insert_relationship(db_session, a["id"], b["id"], RelationshipType.DUPLICATE)
    client.post("/api/v1/issues/discover")

    issue = client.get("/api/v1/issues").json()["items"][0]
    assert issue["status"] == "open"


def test_feedback_processing_status_unaffected_by_discovery(client, db_session) -> None:
    """Issue discovery is a separate, additive layer — it must never touch
    Feedback.processing_status."""
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    _insert_relationship(db_session, a["id"], b["id"], RelationshipType.DUPLICATE)

    client.post("/api/v1/issues/discover")

    fetched = client.get(f"/api/v1/feedback/{a['id']}").json()
    assert fetched["processing_status"] == ProcessingStatus.RECEIVED.value

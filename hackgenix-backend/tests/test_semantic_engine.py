import math
import types
import uuid

from sqlalchemy import select

from app.db.models.enums import FeedbackCategory, RelationshipType
from app.db.models.feedback_embedding import FEEDBACK_EMBEDDING_DIMENSION, FeedbackEmbedding
from app.db.models.feedback_relationship import FeedbackRelationship
from app.services.semantic_engine import (
    DEFAULT_SIMILARITY_FLOOR,
    DUPLICATE_SIMILARITY_THRESHOLD,
    RELATED_SIMILARITY_THRESHOLD,
    SEVERITY_DOWNGRADE_GAP,
    classify_relationship,
)


def _create_feedback(client, **overrides) -> dict:
    payload = {"source": "app_store", "raw_text": "The app crashes on upload."}
    payload.update(overrides)
    return client.post("/api/v1/feedback", json=payload).json()


def _unit_vector_at_angle(theta: float, dimension: int = FEEDBACK_EMBEDDING_DIMENSION) -> list[float]:
    """A unit vector at angle `theta` from the reference vector (1, 0, 0, ...)
    within the first two dimensions, zero elsewhere. Since both are unit
    vectors, cosine similarity with the reference vector is exactly
    cos(theta) — this gives exact, deterministic control over similarity
    for tests, unlike a real embedding."""
    vec = [0.0] * dimension
    vec[0] = math.cos(theta)
    vec[1] = math.sin(theta)
    return vec


def _reference_vector() -> list[float]:
    return _unit_vector_at_angle(0.0)


def _vector_with_similarity(similarity: float) -> list[float]:
    theta = math.acos(max(-1.0, min(1.0, similarity)))
    return _unit_vector_at_angle(theta)


def _insert_embedding(db_session, feedback_id: uuid.UUID, vector: list[float]) -> FeedbackEmbedding:
    embedding = FeedbackEmbedding(
        feedback_id=feedback_id, vector=vector, provider="gemini", model="gemini-embedding-2"
    )
    db_session.add(embedding)
    db_session.commit()
    db_session.refresh(embedding)
    return embedding


def _fake_analysis(category: FeedbackCategory, affected_feature: str, severity: int):
    """A lightweight stand-in for a FeedbackAnalysis row — classify_relationship
    only reads these three attributes, so a real ORM row isn't needed for
    the pure classifier unit tests."""
    return types.SimpleNamespace(category=category, affected_feature=affected_feature, severity=severity)


# --- classify_relationship (pure unit tests, no DB) ---------------------------


def test_classify_relationship_similarity_only_duplicate() -> None:
    assert classify_relationship(DUPLICATE_SIMILARITY_THRESHOLD, None, None) == RelationshipType.DUPLICATE
    assert classify_relationship(0.99, None, None) == RelationshipType.DUPLICATE


def test_classify_relationship_similarity_only_related() -> None:
    assert classify_relationship(RELATED_SIMILARITY_THRESHOLD, None, None) == RelationshipType.RELATED
    assert (
        classify_relationship(DUPLICATE_SIMILARITY_THRESHOLD - 0.01, None, None) == RelationshipType.RELATED
    )


def test_classify_relationship_similarity_only_distinct() -> None:
    assert (
        classify_relationship(RELATED_SIMILARITY_THRESHOLD - 0.01, None, None) == RelationshipType.DISTINCT
    )
    assert classify_relationship(0.0, None, None) == RelationshipType.DISTINCT


def test_classify_relationship_downgrades_duplicate_on_category_mismatch() -> None:
    source = _fake_analysis(FeedbackCategory.BUG, "upload", 7)
    candidate = _fake_analysis(FeedbackCategory.FEATURE_REQUEST, "upload", 7)

    result = classify_relationship(0.99, source, candidate)

    assert result == RelationshipType.RELATED  # downgraded from DUPLICATE


def test_classify_relationship_downgrades_related_to_distinct_on_category_mismatch() -> None:
    source = _fake_analysis(FeedbackCategory.BUG, "upload", 7)
    candidate = _fake_analysis(FeedbackCategory.BILLING, "upload", 7)

    result = classify_relationship(RELATED_SIMILARITY_THRESHOLD + 0.01, source, candidate)

    assert result == RelationshipType.DISTINCT


def test_classify_relationship_downgrades_duplicate_on_different_feature() -> None:
    source = _fake_analysis(FeedbackCategory.BUG, "upload", 7)
    candidate = _fake_analysis(FeedbackCategory.BUG, "checkout", 7)

    result = classify_relationship(0.99, source, candidate)

    assert result == RelationshipType.RELATED  # duplicate requires matching feature too


def test_classify_relationship_downgrades_on_severity_gap() -> None:
    source = _fake_analysis(FeedbackCategory.BUG, "upload", 9)
    candidate = _fake_analysis(FeedbackCategory.BUG, "upload", 9 - SEVERITY_DOWNGRADE_GAP)

    result = classify_relationship(0.99, source, candidate)

    assert result == RelationshipType.RELATED  # downgraded from DUPLICATE


def test_classify_relationship_never_upgrades_when_analysis_missing() -> None:
    # Missing analysis on either side must never invent a stronger claim.
    assert classify_relationship(0.5, None, None) == RelationshipType.DISTINCT


# --- find_similar_feedback (real pgvector queries) ----------------------------


def test_find_similar_excludes_self_and_finds_identical_vector(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    _insert_embedding(db_session, a["id"], _reference_vector())
    _insert_embedding(db_session, b["id"], _reference_vector())  # identical -> similarity 1.0

    response = client.get(f"/api/v1/feedback/{a['id']}/similar")

    assert response.status_code == 200
    body = response.json()
    candidate_ids = [c["feedback_id"] for c in body["candidates"]]
    assert a["id"] not in candidate_ids
    assert b["id"] in candidate_ids
    match = next(c for c in body["candidates"] if c["feedback_id"] == b["id"])
    assert match["similarity"] == 1.0
    assert match["relationship_type"] == RelationshipType.DUPLICATE.value


def test_find_similar_orders_by_similarity_descending(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    close = _create_feedback(client, external_id="close")
    far = _create_feedback(client, external_id="far")
    _insert_embedding(db_session, a["id"], _reference_vector())
    _insert_embedding(db_session, close["id"], _vector_with_similarity(0.95))
    _insert_embedding(db_session, far["id"], _vector_with_similarity(0.80))

    response = client.get(f"/api/v1/feedback/{a['id']}/similar", params={"threshold": 0.5})

    ids_in_order = [c["feedback_id"] for c in response.json()["candidates"]]
    assert ids_in_order.index(close["id"]) < ids_in_order.index(far["id"])


def test_find_similar_threshold_behavior(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    borderline = _create_feedback(client, external_id="borderline")
    _insert_embedding(db_session, a["id"], _reference_vector())
    _insert_embedding(db_session, borderline["id"], _vector_with_similarity(0.60))

    excluded = client.get(f"/api/v1/feedback/{a['id']}/similar", params={"threshold": 0.7}).json()
    included = client.get(f"/api/v1/feedback/{a['id']}/similar", params={"threshold": 0.5}).json()

    assert borderline["id"] not in [c["feedback_id"] for c in excluded["candidates"]]
    assert borderline["id"] in [c["feedback_id"] for c in included["candidates"]]


def test_find_similar_respects_candidate_limit(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    _insert_embedding(db_session, a["id"], _reference_vector())
    for i in range(5):
        item = _create_feedback(client, external_id=f"cand-{i}")
        _insert_embedding(db_session, item["id"], _vector_with_similarity(0.9 - i * 0.01))

    response = client.get(f"/api/v1/feedback/{a['id']}/similar", params={"threshold": 0.5, "limit": 2})

    assert len(response.json()["candidates"]) == 2


def test_similarity_score_correctness(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    _insert_embedding(db_session, a["id"], _reference_vector())
    _insert_embedding(db_session, b["id"], _vector_with_similarity(0.8))

    response = client.get(f"/api/v1/feedback/{a['id']}/similar", params={"threshold": 0.5})

    match = next(c for c in response.json()["candidates"] if c["feedback_id"] == b["id"])
    assert abs(match["similarity"] - 0.8) < 0.01


def test_similar_feedback_missing_feedback_returns_404(client) -> None:
    response = client.get(f"/api/v1/feedback/{uuid.uuid4()}/similar")

    assert response.status_code == 404


def test_similar_feedback_missing_embedding_returns_404(client) -> None:
    a = _create_feedback(client)  # no embedding inserted

    response = client.get(f"/api/v1/feedback/{a['id']}/similar")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "embedding_missing"


def test_get_similar_does_not_persist_anything(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    _insert_embedding(db_session, a["id"], _reference_vector())
    _insert_embedding(db_session, b["id"], _reference_vector())

    client.get(f"/api/v1/feedback/{a['id']}/similar")

    count = db_session.execute(select(FeedbackRelationship)).scalars().all()
    assert count == []


def test_similar_response_never_includes_raw_vector(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    _insert_embedding(db_session, a["id"], _reference_vector())
    _insert_embedding(db_session, b["id"], _reference_vector())

    response = client.get(f"/api/v1/feedback/{a['id']}/similar")

    assert '"vector"' not in response.text


# --- POST /semantic-analysis (persistence) ------------------------------------


def test_semantic_analysis_persists_duplicate_relationship(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    _insert_embedding(db_session, a["id"], _reference_vector())
    _insert_embedding(db_session, b["id"], _reference_vector())

    response = client.post(f"/api/v1/feedback/{a['id']}/semantic-analysis")

    assert response.status_code == 200
    body = response.json()
    assert body["candidates_considered"] == 1
    assert len(body["relationships"]) == 1
    assert body["relationships"][0]["relationship_type"] == RelationshipType.DUPLICATE.value

    stored = db_session.execute(select(FeedbackRelationship)).scalars().all()
    assert len(stored) == 1
    assert {str(stored[0].feedback_id_a), str(stored[0].feedback_id_b)} == {a["id"], b["id"]}


def test_semantic_analysis_does_not_persist_distinct(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    _insert_embedding(db_session, a["id"], _reference_vector())
    _insert_embedding(db_session, b["id"], _vector_with_similarity(0.60))  # below RELATED threshold

    response = client.post(f"/api/v1/feedback/{a['id']}/semantic-analysis")

    body = response.json()
    assert body["candidates_considered"] == 1
    assert len(body["relationships"]) == 0

    stored = db_session.execute(select(FeedbackRelationship)).scalars().all()
    assert stored == []


def test_semantic_analysis_running_twice_does_not_duplicate_rows(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    _insert_embedding(db_session, a["id"], _reference_vector())
    _insert_embedding(db_session, b["id"], _reference_vector())

    client.post(f"/api/v1/feedback/{a['id']}/semantic-analysis")
    client.post(f"/api/v1/feedback/{a['id']}/semantic-analysis")

    stored = db_session.execute(select(FeedbackRelationship)).scalars().all()
    assert len(stored) == 1


def test_semantic_analysis_prevents_reverse_relationship_row(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    _insert_embedding(db_session, a["id"], _reference_vector())
    _insert_embedding(db_session, b["id"], _reference_vector())

    client.post(f"/api/v1/feedback/{a['id']}/semantic-analysis")  # discovers A~B
    client.post(f"/api/v1/feedback/{b['id']}/semantic-analysis")  # would discover B~A

    stored = db_session.execute(select(FeedbackRelationship)).scalars().all()
    assert len(stored) == 1  # not two rows for the same unordered pair


def test_semantic_analysis_missing_feedback_returns_404(client) -> None:
    response = client.post(f"/api/v1/feedback/{uuid.uuid4()}/semantic-analysis")

    assert response.status_code == 404


def test_semantic_analysis_missing_embedding_returns_404(client) -> None:
    a = _create_feedback(client)

    response = client.post(f"/api/v1/feedback/{a['id']}/semantic-analysis")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "embedding_missing"


def test_semantic_analysis_response_never_includes_raw_vector(client, db_session) -> None:
    a = _create_feedback(client, external_id="a")
    b = _create_feedback(client, external_id="b")
    _insert_embedding(db_session, a["id"], _reference_vector())
    _insert_embedding(db_session, b["id"], _reference_vector())

    response = client.post(f"/api/v1/feedback/{a['id']}/semantic-analysis")

    assert '"vector"' not in response.text

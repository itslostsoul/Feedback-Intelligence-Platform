import uuid

from app.api.dependencies import get_embedding_provider
from app.db.models.enums import ProcessingStatus
from app.db.models.feedback_embedding import FEEDBACK_EMBEDDING_DIMENSION
from app.main import app
from tests.fakes import FakeEmbeddingProvider


def _create_feedback(client, **overrides) -> dict:
    payload = {"source": "app_store", "raw_text": "The app crashes on upload."}
    payload.update(overrides)
    return client.post("/api/v1/feedback", json=payload).json()


def test_embed_feedback_success(client) -> None:
    created = _create_feedback(client)

    response = client.post(f"/api/v1/feedback/{created['id']}/embed")

    assert response.status_code == 200
    body = response.json()
    assert body["feedback_id"] == created["id"]
    assert body["was_generated"] is True
    assert body["embedding"]["feedback_id"] == created["id"]
    assert body["embedding"]["dimension"] == FEEDBACK_EMBEDDING_DIMENSION
    assert body["embedding"]["provider"] == "gemini"
    assert body["embedding"]["model"] == "gemini-embedding-2"
    # The raw vector is never returned as a giant JSON array.
    assert "vector" not in body["embedding"]


def test_embed_feedback_does_not_return_raw_vector_array(client) -> None:
    created = _create_feedback(client)

    response = client.post(f"/api/v1/feedback/{created['id']}/embed")

    raw_body = response.text
    assert '"vector"' not in raw_body


def test_embed_feedback_is_idempotent_and_avoids_duplicate_generation(client) -> None:
    created = _create_feedback(client)
    fake_provider = FakeEmbeddingProvider()
    app.dependency_overrides[get_embedding_provider] = lambda: fake_provider

    first = client.post(f"/api/v1/feedback/{created['id']}/embed")
    second = client.post(f"/api/v1/feedback/{created['id']}/embed")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["was_generated"] is True
    assert second.json()["was_generated"] is False
    assert first.json()["embedding"]["id"] == second.json()["embedding"]["id"]
    # The provider was only ever called once — the second request reused
    # the existing row instead of generating again.
    assert fake_provider.call_count == 1


def test_embed_nonexistent_feedback_returns_404(client) -> None:
    response = client.post(f"/api/v1/feedback/{uuid.uuid4()}/embed")

    assert response.status_code == 404


def test_embed_feedback_provider_failure_returns_502(client) -> None:
    created = _create_feedback(client)
    app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider(
        raise_provider_error=True
    )

    response = client.post(f"/api/v1/feedback/{created['id']}/embed")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "embedding_provider_error"


def test_embed_feedback_invalid_output_returns_502(client) -> None:
    created = _create_feedback(client)
    app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider(
        raise_output_error=True
    )

    response = client.post(f"/api/v1/feedback/{created['id']}/embed")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "embedding_output_validation_error"


def test_embed_feedback_wrong_dimension_returns_502(client) -> None:
    created = _create_feedback(client)
    app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider(
        vector=[0.1, 0.2, 0.3]  # wrong dimension for the fixed pgvector column
    )

    response = client.post(f"/api/v1/feedback/{created['id']}/embed")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "embedding_output_validation_error"


def test_embedding_is_independent_of_analysis_lifecycle(client) -> None:
    """A feedback item can be embedded without ever being analyzed, and the
    embed call must never touch processing_status."""
    created = _create_feedback(client)
    assert created["processing_status"] == ProcessingStatus.RECEIVED.value

    client.post(f"/api/v1/feedback/{created['id']}/embed")

    fetched = client.get(f"/api/v1/feedback/{created['id']}").json()
    assert fetched["processing_status"] == ProcessingStatus.RECEIVED.value


def test_analysis_and_embedding_can_coexist_independently(client) -> None:
    created = _create_feedback(client)

    analyze_response = client.post(f"/api/v1/feedback/{created['id']}/analyze")
    embed_response = client.post(f"/api/v1/feedback/{created['id']}/embed")

    assert analyze_response.status_code == 200
    assert embed_response.status_code == 200
    assert analyze_response.json()["processing_status"] == ProcessingStatus.ANALYZED.value


def test_embed_feedback_missing_api_key_returns_503(client) -> None:
    from app.core.errors import EmbeddingConfigurationError

    created = _create_feedback(client)

    def _raise_configuration_error():
        raise EmbeddingConfigurationError("GEMINI_API_KEY is not configured.")

    app.dependency_overrides[get_embedding_provider] = _raise_configuration_error

    response = client.post(f"/api/v1/feedback/{created['id']}/embed")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "embedding_configuration_error"


def test_embed_feedback_does_not_log_raw_text(client, caplog) -> None:
    raw_text = "SECRET_MARKER_TEXT_EMBED_98765 crashes on upload"
    created = _create_feedback(client, raw_text=raw_text)

    with caplog.at_level("DEBUG"):
        client.post(f"/api/v1/feedback/{created['id']}/embed")

    for record in caplog.records:
        assert raw_text not in record.getMessage()

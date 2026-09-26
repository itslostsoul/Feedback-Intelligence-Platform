import uuid

from app.api.dependencies import get_llm_provider
from app.core.errors import LLMConfigurationError
from app.db.models.enums import FeedbackCategory, ProcessingStatus
from app.main import app
from app.schemas.analysis import FeedbackAnalysisResult
from tests.fakes import FakeLLMProvider


def _create_feedback(client, **overrides) -> dict:
    payload = {"source": "app_store", "raw_text": "The app crashes on upload."}
    payload.update(overrides)
    return client.post("/api/v1/feedback", json=payload).json()


def test_analyze_feedback_success(client) -> None:
    created = _create_feedback(client)

    response = client.post(f"/api/v1/feedback/{created['id']}/analyze")

    assert response.status_code == 200
    body = response.json()
    assert body["feedback_id"] == created["id"]
    assert body["processing_status"] == ProcessingStatus.ANALYZED.value
    assert body["analysis"]["category"] == FeedbackCategory.BUG.value
    assert body["analysis"]["provider"] == "gemini"
    assert body["analysis"]["feedback_id"] == created["id"]

    fetched = client.get(f"/api/v1/feedback/{created['id']}").json()
    assert fetched["processing_status"] == ProcessingStatus.ANALYZED.value


def test_analyze_feedback_low_confidence_needs_review(client) -> None:
    created = _create_feedback(client)
    low_confidence_result = FeedbackAnalysisResult(
        category=FeedbackCategory.UI_FRICTION,
        affected_feature="settings page",
        severity=3,
        confidence=0.2,
        summary="Settings toggle is confusing.",
    )
    app.dependency_overrides[get_llm_provider] = lambda: FakeLLMProvider(result=low_confidence_result)

    response = client.post(f"/api/v1/feedback/{created['id']}/analyze")

    assert response.status_code == 200
    assert response.json()["processing_status"] == ProcessingStatus.NEEDS_REVIEW.value


def test_analyze_feedback_provider_failure_marks_failed(client) -> None:
    created = _create_feedback(client)
    app.dependency_overrides[get_llm_provider] = lambda: FakeLLMProvider(raise_provider_error=True)

    response = client.post(f"/api/v1/feedback/{created['id']}/analyze")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "llm_provider_error"

    fetched = client.get(f"/api/v1/feedback/{created['id']}").json()
    assert fetched["processing_status"] == ProcessingStatus.FAILED.value


def test_analyze_feedback_invalid_output_marks_failed(client) -> None:
    created = _create_feedback(client)
    app.dependency_overrides[get_llm_provider] = lambda: FakeLLMProvider(raise_output_error=True)

    response = client.post(f"/api/v1/feedback/{created['id']}/analyze")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "llm_output_validation_error"

    fetched = client.get(f"/api/v1/feedback/{created['id']}").json()
    assert fetched["processing_status"] == ProcessingStatus.FAILED.value


def test_analyze_nonexistent_feedback_returns_404(client) -> None:
    response = client.post(f"/api/v1/feedback/{uuid.uuid4()}/analyze")

    assert response.status_code == 404


def test_analyze_feedback_missing_api_key_returns_503_without_touching_status(client) -> None:
    created = _create_feedback(client)

    def _raise_configuration_error():
        raise LLMConfigurationError("GEMINI_API_KEY is not configured.")

    app.dependency_overrides[get_llm_provider] = _raise_configuration_error

    response = client.post(f"/api/v1/feedback/{created['id']}/analyze")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_configuration_error"

    # Configuration errors happen before any status transition is attempted.
    fetched = client.get(f"/api/v1/feedback/{created['id']}").json()
    assert fetched["processing_status"] == ProcessingStatus.RECEIVED.value


def test_analyze_feedback_reanalysis_overwrites_existing_analysis(client) -> None:
    created = _create_feedback(client)
    client.post(f"/api/v1/feedback/{created['id']}/analyze")

    new_result = FeedbackAnalysisResult(
        category=FeedbackCategory.PERFORMANCE,
        affected_feature="checkout",
        severity=9,
        confidence=0.95,
        summary="Checkout times out under load.",
    )
    app.dependency_overrides[get_llm_provider] = lambda: FakeLLMProvider(result=new_result)

    response = client.post(f"/api/v1/feedback/{created['id']}/analyze")

    assert response.status_code == 200
    body = response.json()["analysis"]
    assert body["category"] == FeedbackCategory.PERFORMANCE.value
    assert body["severity"] == 9


def test_analyze_feedback_does_not_log_raw_text(client, caplog) -> None:
    raw_text = "SECRET_MARKER_TEXT_12345 crashes on upload"
    created = _create_feedback(client, raw_text=raw_text)

    with caplog.at_level("DEBUG"):
        client.post(f"/api/v1/feedback/{created['id']}/analyze")

    for record in caplog.records:
        assert raw_text not in record.getMessage()


def test_get_analysis_returns_persisted_result_without_reanalyzing(client) -> None:
    created = _create_feedback(client)
    client.post(f"/api/v1/feedback/{created['id']}/analyze")

    response = client.get(f"/api/v1/feedback/{created['id']}/analysis")

    assert response.status_code == 200
    body = response.json()
    assert body["feedback_id"] == created["id"]
    assert body["category"] == FeedbackCategory.BUG.value
    assert body["provider"] == "gemini"


def test_get_analysis_missing_returns_404_analysis_missing(client) -> None:
    created = _create_feedback(client)

    response = client.get(f"/api/v1/feedback/{created['id']}/analysis")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "analysis_missing"


def test_get_analysis_nonexistent_feedback_returns_404_not_found(client) -> None:
    response = client.get(f"/api/v1/feedback/{uuid.uuid4()}/analysis")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"

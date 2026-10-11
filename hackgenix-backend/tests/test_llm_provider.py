import pytest

from app.core.config import Settings
from app.core.errors import LLMConfigurationError, LLMOutputValidationError, LLMProviderError
from app.db.models.enums import FeedbackCategory
from app.schemas.analysis import FeedbackAnalysisResult
from app.services.llm.factory import build_llm_provider
from app.services.llm.gemini_provider import GeminiProvider


def _settings(**overrides) -> Settings:
    base = {
        "DATABASE_URL": "postgresql+psycopg://user:pass@localhost/testdb",
        "LLM_PROVIDER": "gemini",
        "LLM_MODEL": "gemini-3.8-flash",
        "GEMINI_API_KEY": "test-key",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


# --- factory / configuration -------------------------------------------------


def test_factory_requires_provider() -> None:
    with pytest.raises(LLMConfigurationError):
        build_llm_provider(_settings(LLM_PROVIDER=None))


def test_factory_rejects_unsupported_provider() -> None:
    with pytest.raises(LLMConfigurationError):
        build_llm_provider(_settings(LLM_PROVIDER="openai"))


def test_factory_requires_api_key_for_gemini() -> None:
    with pytest.raises(LLMConfigurationError):
        build_llm_provider(_settings(GEMINI_API_KEY=None))


def test_factory_requires_model() -> None:
    with pytest.raises(LLMConfigurationError):
        build_llm_provider(_settings(LLM_MODEL=None))


def test_factory_builds_gemini_provider() -> None:
    provider = build_llm_provider(_settings())
    assert isinstance(provider, GeminiProvider)


# --- structured output contract ----------------------------------------------


def test_analysis_result_rejects_out_of_range_severity() -> None:
    with pytest.raises(ValueError):
        FeedbackAnalysisResult(
            category="bug", affected_feature="upload", severity=11, confidence=0.5, summary="x"
        )


def test_analysis_result_rejects_out_of_range_confidence() -> None:
    with pytest.raises(ValueError):
        FeedbackAnalysisResult(
            category="bug", affected_feature="upload", severity=5, confidence=1.5, summary="x"
        )


def test_analysis_result_rejects_blank_summary() -> None:
    with pytest.raises(ValueError):
        FeedbackAnalysisResult(
            category="bug", affected_feature="upload", severity=5, confidence=0.5, summary="   "
        )


def test_analysis_result_rejects_unknown_category() -> None:
    with pytest.raises(ValueError):
        FeedbackAnalysisResult(
            category="not_a_real_category",
            affected_feature="upload",
            severity=5,
            confidence=0.5,
            summary="x",
        )


def test_analysis_result_rejects_aggregate_fields_the_llm_must_not_compute() -> None:
    """category/severity/confidence/summary/affected_feature only — no
    frequency, trend, priority, etc. (extra="forbid")."""
    with pytest.raises(ValueError):
        FeedbackAnalysisResult(
            category="bug",
            affected_feature="upload",
            severity=5,
            confidence=0.5,
            summary="x",
            frequency=42,
        )


# --- GeminiProvider (network mocked out) --------------------------------------


class _FakeResponse:
    def __init__(self, text: str | None) -> None:
        self.text = text


def test_gemini_provider_parses_valid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GeminiProvider(api_key="test-key", model="gemini-3.8-flash")
    monkeypatch.setattr(
        provider._client.models,
        "generate_content",
        lambda **kwargs: _FakeResponse(
            '{"category":"bug","affected_feature":"upload","severity":7,'
            '"confidence":0.9,"summary":"Fails on large uploads."}'
        ),
    )

    result = provider.analyze_feedback("The upload fails")

    assert result.category == FeedbackCategory.BUG
    assert result.severity == 7
    assert result.confidence == 0.9


def test_gemini_provider_rejects_non_json_output(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GeminiProvider(api_key="test-key", model="gemini-3.8-flash")
    monkeypatch.setattr(
        provider._client.models, "generate_content", lambda **kwargs: _FakeResponse("not json")
    )

    with pytest.raises(LLMOutputValidationError):
        provider.analyze_feedback("some text")


def test_gemini_provider_rejects_schema_violating_json(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GeminiProvider(api_key="test-key", model="gemini-3.8-flash")
    monkeypatch.setattr(
        provider._client.models,
        "generate_content",
        lambda **kwargs: _FakeResponse('{"category":"bug","severity":99}'),
    )

    with pytest.raises(LLMOutputValidationError):
        provider.analyze_feedback("some text")


def test_gemini_provider_rejects_empty_output(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GeminiProvider(api_key="test-key", model="gemini-3.8-flash")
    monkeypatch.setattr(provider._client.models, "generate_content", lambda **kwargs: _FakeResponse(""))

    with pytest.raises(LLMOutputValidationError):
        provider.analyze_feedback("some text")


def test_gemini_provider_wraps_sdk_exceptions_as_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GeminiProvider(api_key="test-key", model="gemini-3.8-flash")

    def _boom(**kwargs):
        raise RuntimeError("simulated network/timeout/rate-limit failure")

    monkeypatch.setattr(provider._client.models, "generate_content", _boom)

    with pytest.raises(LLMProviderError):
        provider.analyze_feedback("some text")

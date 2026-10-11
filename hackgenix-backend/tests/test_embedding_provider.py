import pytest

from app.core.config import Settings
from app.core.errors import EmbeddingConfigurationError, EmbeddingOutputValidationError, EmbeddingProviderError
from app.schemas.embedding import EmbeddingResult
from app.services.embedding.factory import build_embedding_provider
from app.services.embedding.gemini_provider import GeminiEmbeddingProvider


def _settings(**overrides) -> Settings:
    base = {
        "DATABASE_URL": "postgresql+psycopg://user:pass@localhost/testdb",
        "EMBEDDING_PROVIDER": "gemini",
        "EMBEDDING_MODEL": "gemini-embedding-2",
        "GEMINI_API_KEY": "test-key",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


# --- factory / configuration -------------------------------------------------


def test_factory_requires_provider() -> None:
    with pytest.raises(EmbeddingConfigurationError):
        build_embedding_provider(_settings(EMBEDDING_PROVIDER=None))


def test_factory_rejects_unsupported_provider() -> None:
    with pytest.raises(EmbeddingConfigurationError):
        build_embedding_provider(_settings(EMBEDDING_PROVIDER="openai"))


def test_factory_requires_api_key_for_gemini() -> None:
    with pytest.raises(EmbeddingConfigurationError):
        build_embedding_provider(_settings(GEMINI_API_KEY=None))


def test_factory_requires_model() -> None:
    with pytest.raises(EmbeddingConfigurationError):
        build_embedding_provider(_settings(EMBEDDING_MODEL=None))


def test_factory_builds_gemini_embedding_provider() -> None:
    provider = build_embedding_provider(_settings())
    assert isinstance(provider, GeminiEmbeddingProvider)


def test_factory_reuses_gemini_api_key_no_separate_secret_needed() -> None:
    """Only GEMINI_API_KEY is required — there is no separate
    EMBEDDING_GEMINI_API_KEY field to configure."""
    settings = _settings(GEMINI_API_KEY="shared-key")
    provider = build_embedding_provider(settings)
    assert isinstance(provider, GeminiEmbeddingProvider)


# --- structured output contract ----------------------------------------------


def test_embedding_result_rejects_empty_vector() -> None:
    with pytest.raises(ValueError):
        EmbeddingResult(vector=[])


def test_embedding_result_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError):
        EmbeddingResult(vector=[0.1, float("nan"), 0.3])

    with pytest.raises(ValueError):
        EmbeddingResult(vector=[0.1, float("inf"), 0.3])


def test_embedding_result_exposes_dimension() -> None:
    result = EmbeddingResult(vector=[0.1, 0.2, 0.3])
    assert result.dimension == 3


# --- GeminiEmbeddingProvider (network mocked out) -----------------------------


class _FakeEmbeddingValues:
    def __init__(self, values: list[float] | None) -> None:
        self.values = values


class _FakeEmbedResponse:
    def __init__(self, embeddings: list[_FakeEmbeddingValues]) -> None:
        self.embeddings = embeddings


def test_gemini_embedding_provider_parses_valid_response(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GeminiEmbeddingProvider(api_key="test-key", model="gemini-embedding-2")
    monkeypatch.setattr(
        provider._client.models,
        "embed_content",
        lambda **kwargs: _FakeEmbedResponse([_FakeEmbeddingValues([0.1, 0.2, 0.3])]),
    )

    result = provider.embed_feedback("The app crashes on upload.")

    assert result.vector == [0.1, 0.2, 0.3]
    assert result.dimension == 3


def test_gemini_embedding_provider_rejects_empty_embeddings_list(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GeminiEmbeddingProvider(api_key="test-key", model="gemini-embedding-2")
    monkeypatch.setattr(provider._client.models, "embed_content", lambda **kwargs: _FakeEmbedResponse([]))

    with pytest.raises(EmbeddingOutputValidationError):
        provider.embed_feedback("some text")


def test_gemini_embedding_provider_rejects_empty_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GeminiEmbeddingProvider(api_key="test-key", model="gemini-embedding-2")
    monkeypatch.setattr(
        provider._client.models,
        "embed_content",
        lambda **kwargs: _FakeEmbedResponse([_FakeEmbeddingValues([])]),
    )

    with pytest.raises(EmbeddingOutputValidationError):
        provider.embed_feedback("some text")


def test_gemini_embedding_provider_wraps_sdk_exceptions_as_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GeminiEmbeddingProvider(api_key="test-key", model="gemini-embedding-2")

    def _boom(**kwargs):
        raise RuntimeError("simulated network/timeout/rate-limit failure")

    monkeypatch.setattr(provider._client.models, "embed_content", _boom)

    with pytest.raises(EmbeddingProviderError):
        provider.embed_feedback("some text")

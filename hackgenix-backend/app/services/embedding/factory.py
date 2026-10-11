from app.core.config import Settings, get_settings
from app.core.errors import EmbeddingConfigurationError
from app.services.embedding.base import EmbeddingProvider

SUPPORTED_PROVIDERS = {"gemini"}


def build_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    """Builds the configured embedding provider from settings.

    Raises `EmbeddingConfigurationError` for anything that is a setup
    problem (missing/unsupported provider, missing API key) rather than a
    per-item embedding failure.
    """
    settings = settings or get_settings()
    provider_name = (settings.EMBEDDING_PROVIDER or "").strip().lower()

    if not provider_name:
        raise EmbeddingConfigurationError("EMBEDDING_PROVIDER is not configured.")
    if provider_name not in SUPPORTED_PROVIDERS:
        raise EmbeddingConfigurationError(f"Unsupported embedding provider: {provider_name!r}.")

    if not settings.EMBEDDING_MODEL:
        raise EmbeddingConfigurationError("EMBEDDING_MODEL is not configured.")

    if provider_name == "gemini":
        # Deliberately reuses GEMINI_API_KEY — same Gemini account/credential
        # as the LLM analysis provider, just a different model. No separate
        # embedding-specific key is introduced.
        if not settings.GEMINI_API_KEY:
            raise EmbeddingConfigurationError("GEMINI_API_KEY is not configured.")

        from app.services.embedding.gemini_provider import GeminiEmbeddingProvider

        return GeminiEmbeddingProvider(
            api_key=settings.GEMINI_API_KEY,
            model=settings.EMBEDDING_MODEL,
            timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
        )

    raise EmbeddingConfigurationError(f"Unsupported embedding provider: {provider_name!r}.")

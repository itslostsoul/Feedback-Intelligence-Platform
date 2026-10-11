from app.core.config import Settings, get_settings
from app.core.errors import LLMConfigurationError
from app.services.llm.base import LLMProvider

SUPPORTED_PROVIDERS = {"gemini"}


def build_llm_provider(settings: Settings | None = None) -> LLMProvider:
    """Builds the configured LLM provider from settings.

    Raises `LLMConfigurationError` for anything that is a setup problem
    (missing/unsupported provider, missing API key) rather than a per-item
    analysis failure.
    """
    settings = settings or get_settings()
    provider_name = (settings.LLM_PROVIDER or "").strip().lower()

    if not provider_name:
        raise LLMConfigurationError("LLM_PROVIDER is not configured.")
    if provider_name not in SUPPORTED_PROVIDERS:
        raise LLMConfigurationError(f"Unsupported LLM provider: {provider_name!r}.")

    if not settings.LLM_MODEL:
        raise LLMConfigurationError("LLM_MODEL is not configured.")

    if provider_name == "gemini":
        if not settings.GEMINI_API_KEY:
            raise LLMConfigurationError("GEMINI_API_KEY is not configured.")

        from app.services.llm.gemini_provider import GeminiProvider

        return GeminiProvider(
            api_key=settings.GEMINI_API_KEY,
            model=settings.LLM_MODEL,
            timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
        )

    raise LLMConfigurationError(f"Unsupported LLM provider: {provider_name!r}.")

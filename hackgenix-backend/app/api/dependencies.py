from app.services.embedding.base import EmbeddingProvider
from app.services.embedding.factory import build_embedding_provider
from app.services.llm.base import LLMProvider
from app.services.llm.factory import build_llm_provider


def get_llm_provider() -> LLMProvider:
    """FastAPI dependency; overridden in tests with a fake provider so no
    test requires a real Gemini API call."""
    return build_llm_provider()


def get_embedding_provider() -> EmbeddingProvider:
    """FastAPI dependency; overridden in tests with a fake provider so no
    test requires a real Gemini API call."""
    return build_embedding_provider()

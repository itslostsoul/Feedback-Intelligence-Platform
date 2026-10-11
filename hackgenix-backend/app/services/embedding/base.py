from typing import Protocol

from app.schemas.embedding import EmbeddingResult


class EmbeddingProvider(Protocol):
    """Everything the rest of the app knows about an embedding provider.

    Mirrors `app.services.llm.base.LLMProvider`'s shape: the application
    depends on this interface only, never on a specific provider SDK.
    `GeminiEmbeddingProvider` is the first implementation; an OpenAI or
    local (e.g. sentence-transformers) provider is a matter of adding
    another class that satisfies this same shape plus a branch in
    `app.services.embedding.factory`.
    """

    def embed_feedback(self, raw_text: str) -> EmbeddingResult:
        """Embed one feedback item's text.

        Raises `app.core.errors.EmbeddingProviderError` if the provider call
        itself fails (timeout, rate limit, upstream error), or
        `app.core.errors.EmbeddingOutputValidationError` if the provider
        responded but didn't return a well-formed vector.
        """
        ...

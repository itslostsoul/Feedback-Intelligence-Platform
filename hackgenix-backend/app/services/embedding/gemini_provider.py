import logging

from google import genai
from google.genai import types as genai_types
from pydantic import ValidationError

from app.core.errors import EmbeddingOutputValidationError, EmbeddingProviderError
from app.schemas.embedding import EmbeddingResult

logger = logging.getLogger(__name__)


class GeminiEmbeddingProvider:
    """EmbeddingProvider implementation backed by Google's Gemini API.

    Never logs the API key or raw feedback text — only outcome metadata
    (error type, vector dimension) is logged.
    """

    def __init__(self, api_key: str, model: str, timeout_seconds: float = 30.0) -> None:
        self._model = model
        self._client = genai.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(timeout=int(timeout_seconds * 1000)),
        )

    def embed_feedback(self, raw_text: str) -> EmbeddingResult:
        try:
            response = self._client.models.embed_content(model=self._model, contents=raw_text)
        except Exception as exc:
            logger.warning("gemini_embedding_request_failed error_type=%s", type(exc).__name__)
            raise EmbeddingProviderError("The embedding provider request failed.") from exc

        embeddings = getattr(response, "embeddings", None) or []
        if not embeddings:
            raise EmbeddingOutputValidationError("The embedding provider returned no embeddings.")

        values = list(embeddings[0].values or [])

        try:
            return EmbeddingResult(vector=values)
        except ValidationError as exc:
            logger.warning("gemini_embedding_output_invalid")
            raise EmbeddingOutputValidationError(
                "The embedding provider returned an invalid vector."
            ) from exc

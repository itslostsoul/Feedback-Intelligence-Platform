import math
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class EmbeddingResult(BaseModel):
    """The structured contract every EmbeddingProvider must satisfy: a
    non-empty vector of finite floats.

    Deliberately provider-agnostic — it doesn't hardcode a dimension, since
    different providers/models produce different sizes. Whether a given
    vector's length matches what a specific storage column expects (e.g.
    `FEEDBACK_EMBEDDING_DIMENSION`) is checked by the persistence layer, not
    here.
    """

    vector: list[float]

    @field_validator("vector")
    @classmethod
    def _must_be_finite_and_non_empty(cls, value: list[float]) -> list[float]:
        if not value:
            raise ValueError("embedding vector must not be empty")
        if not all(math.isfinite(x) for x in value):
            raise ValueError("embedding vector must contain only finite numbers")
        return value

    @property
    def dimension(self) -> int:
        return len(self.vector)


class FeedbackEmbeddingRead(BaseModel):
    """API representation of a persisted embedding row.

    Deliberately excludes `vector` (3072 floats) — see
    `EmbedFeedbackResponse` for why the raw array isn't returned by default.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    feedback_id: uuid.UUID
    dimension: int
    provider: str
    model: str
    created_at: datetime
    updated_at: datetime


class EmbedFeedbackResponse(BaseModel):
    feedback_id: uuid.UUID
    embedding: FeedbackEmbeddingRead
    was_generated: bool  # False if an embedding already existed and was reused

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import EmbeddingOutputValidationError
from app.db.models.feedback import Feedback
from app.db.models.feedback_embedding import FEEDBACK_EMBEDDING_DIMENSION, FeedbackEmbedding
from app.services.embedding.base import EmbeddingProvider

logger = logging.getLogger(__name__)


def get_embedding_for_feedback(db: Session, feedback_id) -> FeedbackEmbedding | None:
    stmt = select(FeedbackEmbedding).where(FeedbackEmbedding.feedback_id == feedback_id)
    return db.execute(stmt).scalar_one_or_none()


def embed_feedback(
    db: Session,
    feedback: Feedback,
    provider: EmbeddingProvider,
    settings: Settings | None = None,
) -> tuple[FeedbackEmbedding, bool]:
    """Generates (or reuses) the embedding for one feedback item.

    Returns (embedding, was_generated). If an embedding already exists, it
    is returned as-is and the provider is never called — this is a
    completely separate, independent operation from LLM analysis: it never
    reads or writes `Feedback.processing_status` or `FeedbackAnalysis`, and
    a feedback item may have either, both, or neither.
    """
    settings = settings or get_settings()

    existing = get_embedding_for_feedback(db, feedback.id)
    if existing is not None:
        logger.info("feedback_embedding_reused id=%s", feedback.id)
        return existing, False

    result = provider.embed_feedback(feedback.raw_text)

    if result.dimension != FEEDBACK_EMBEDDING_DIMENSION:
        logger.warning(
            "embedding_dimension_mismatch id=%s expected=%s actual=%s",
            feedback.id,
            FEEDBACK_EMBEDDING_DIMENSION,
            result.dimension,
        )
        raise EmbeddingOutputValidationError(
            f"Embedding provider returned a {result.dimension}-dimensional vector; "
            f"expected {FEEDBACK_EMBEDDING_DIMENSION}."
        )

    embedding = FeedbackEmbedding(
        feedback_id=feedback.id,
        vector=result.vector,
        provider=settings.EMBEDDING_PROVIDER,
        model=settings.EMBEDDING_MODEL,
    )
    db.add(embedding)
    db.commit()
    db.refresh(embedding)
    logger.info("feedback_embedding_created id=%s dim=%s", feedback.id, result.dimension)
    return embedding, True

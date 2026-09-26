import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import LLMOutputValidationError, LLMProviderError
from app.db.models.enums import ProcessingStatus
from app.db.models.feedback import Feedback
from app.db.models.feedback_analysis import FeedbackAnalysis
from app.services.llm.base import LLMProvider

logger = logging.getLogger(__name__)


def get_analysis_for_feedback(db: Session, feedback_id) -> FeedbackAnalysis | None:
    stmt = select(FeedbackAnalysis).where(FeedbackAnalysis.feedback_id == feedback_id)
    return db.execute(stmt).scalar_one_or_none()


def analyze_feedback(
    db: Session,
    feedback: Feedback,
    provider: LLMProvider,
    settings: Settings | None = None,
) -> Feedback:
    """Runs LLM analysis for one feedback item end to end.

    RECEIVED/PENDING_ANALYSIS/... -> ANALYZING -> ANALYZED or NEEDS_REVIEW
    (low confidence), or -> FAILED if the provider call or output validation
    fails. A failure is never silently treated as success: on failure this
    persists processing_status=FAILED and re-raises the same exception, so
    the caller (the API route) surfaces it as an error response.
    """
    settings = settings or get_settings()

    feedback.processing_status = ProcessingStatus.ANALYZING
    db.add(feedback)
    db.commit()
    db.refresh(feedback)

    try:
        result = provider.analyze_feedback(feedback.raw_text)
    except (LLMProviderError, LLMOutputValidationError):
        logger.warning("feedback_analysis_failed id=%s", feedback.id)
        feedback.processing_status = ProcessingStatus.FAILED
        db.add(feedback)
        db.commit()
        db.refresh(feedback)
        raise

    new_status = (
        ProcessingStatus.NEEDS_REVIEW
        if result.confidence < settings.LLM_CONFIDENCE_REVIEW_THRESHOLD
        else ProcessingStatus.ANALYZED
    )

    existing = get_analysis_for_feedback(db, feedback.id)
    if existing is not None:
        existing.category = result.category
        existing.affected_feature = result.affected_feature
        existing.severity = result.severity
        existing.confidence = result.confidence
        existing.summary = result.summary
        existing.provider = settings.LLM_PROVIDER
        existing.model = settings.LLM_MODEL
        db.add(existing)
    else:
        db.add(
            FeedbackAnalysis(
                feedback_id=feedback.id,
                category=result.category,
                affected_feature=result.affected_feature,
                severity=result.severity,
                confidence=result.confidence,
                summary=result.summary,
                provider=settings.LLM_PROVIDER,
                model=settings.LLM_MODEL,
            )
        )

    feedback.processing_status = new_status
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    logger.info(
        "feedback_analysis_complete id=%s status=%s category=%s severity=%s",
        feedback.id,
        new_status.value,
        result.category.value,
        result.severity,
    )
    return feedback

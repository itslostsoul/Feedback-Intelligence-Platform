import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.models.enums import FeedbackCategory


class FeedbackAnalysis(Base):
    """Structured LLM analysis of a single feedback item.

    One row per feedback item — re-analysis overwrites this row rather than
    keeping history. Only interpretation of the individual item is stored
    here (category, affected feature, severity, confidence, summary); any
    cross-feedback aggregation (frequency, trend, priority, product health,
    ...) is explicitly deferred to a later, deterministic backend phase and
    has no place in this table.
    """

    __tablename__ = "feedback_analysis"
    __table_args__ = (
        UniqueConstraint("feedback_id", name="uq_feedback_analysis_feedback_id"),
        CheckConstraint("severity >= 1 AND severity <= 10", name="ck_feedback_analysis_severity_range"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_feedback_analysis_confidence_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    feedback_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("feedback.id", ondelete="CASCADE"), nullable=False
    )

    category: Mapped[FeedbackCategory] = mapped_column(
        Enum(FeedbackCategory, name="feedback_category", native_enum=False, validate_strings=True),
        nullable=False,
    )
    affected_feature: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)

    # Which provider/model produced this analysis — not the raw response.
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)

    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<FeedbackAnalysis feedback_id={self.feedback_id} category={self.category}>"

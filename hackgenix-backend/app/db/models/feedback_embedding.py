import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

# gemini-embedding-2's default output size, confirmed via a live call
# (see scripts/manual_embedding_check.py). Fixed here because pgvector
# columns are a fixed dimension at table-definition time; a future provider
# with a different dimension needs its own migration, not a code change.
FEEDBACK_EMBEDDING_DIMENSION = 3072


class FeedbackEmbedding(Base):
    """A single vector embedding of one feedback item's raw text.

    One row per feedback item — regenerating overwrites this row rather
    than keeping history (mirrors `FeedbackAnalysis`). Stores only the
    vector and provider/model metadata; the feedback text itself lives in
    `feedback` and is never duplicated here.
    """

    __tablename__ = "feedback_embedding"
    __table_args__ = (UniqueConstraint("feedback_id", name="uq_feedback_embedding_feedback_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    feedback_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("feedback.id", ondelete="CASCADE"), nullable=False
    )

    vector: Mapped[list[float]] = mapped_column(Vector(FEEDBACK_EMBEDDING_DIMENSION), nullable=False)

    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @property
    def dimension(self) -> int:
        return len(self.vector)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<FeedbackEmbedding feedback_id={self.feedback_id} dim={self.dimension}>"

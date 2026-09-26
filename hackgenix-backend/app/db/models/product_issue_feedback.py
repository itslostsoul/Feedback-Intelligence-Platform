import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class ProductIssueFeedback(Base):
    """Membership: which feedback items support a given ProductIssue.

    Modeled as a proper many-to-many join table even though this phase
    enforces "at most one issue per feedback item" via the unique constraint
    on `feedback_id` — so the architecture can evolve to true many-to-many
    later by dropping just that one constraint, with no redesign needed.

    This table (plus `feedback`/`feedback_analysis`) is deliberately "enough
    membership information" for a future Signal Engine to deterministically
    compute frequency, severity distribution, categories, affected features,
    source distribution, customer segments, and time distribution — none of
    which are computed or stored here.
    """

    __tablename__ = "product_issue_feedback"
    __table_args__ = (
        UniqueConstraint("feedback_id", name="uq_product_issue_feedback_feedback_id"),
        Index("ix_product_issue_feedback_product_issue_id", "product_issue_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    product_issue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_issue.id", ondelete="CASCADE"), nullable=False
    )
    feedback_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("feedback.id", ondelete="CASCADE"), nullable=False
    )

    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ProductIssueFeedback issue={self.product_issue_id} feedback={self.feedback_id}>"

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.models.enums import FeedbackCategory, ProductIssueStatus


class ProductIssue(Base):
    """A higher-level product issue discovered by clustering related
    feedback (Phase 3B-3). This is an **evidence container**, not a priority
    score or a product-health metric — it groups feedback that appears to
    describe the same underlying problem so a later phase (the Signal
    Engine) can compute frequency, severity distribution, source diversity,
    etc. deterministically from the membership recorded in
    `product_issue_feedback`. Nothing here is a computed aggregate.

    `canonical_feedback_id` is the feedback item that first anchored this
    issue when it was created — kept as a stable reference for
    display/debugging, but reruns look up "is this the same issue as before"
    by existing membership overlap (see
    `app.services.issue_discovery._find_existing_issue_for_members`), not by
    this column, since the canonical member could in principle be filtered
    out of the cluster in a later rerun (see the structured-cohesion filter).
    """

    __tablename__ = "product_issue"
    __table_args__ = (
        UniqueConstraint("canonical_feedback_id", name="uq_product_issue_canonical_feedback_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    canonical_feedback_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("feedback.id", ondelete="CASCADE"), nullable=False
    )

    title: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[FeedbackCategory | None] = mapped_column(
        Enum(FeedbackCategory, name="feedback_category", native_enum=False, validate_strings=True),
        nullable=True,
    )
    affected_feature: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[ProductIssueStatus] = mapped_column(
        Enum(ProductIssueStatus, name="product_issue_status", native_enum=False, validate_strings=True),
        nullable=False,
        default=ProductIssueStatus.OPEN,
    )

    # Which clustering run/logic version last created or updated this issue —
    # see CLUSTERING_ALGORITHM_VERSION in app.services.issue_discovery.
    clustering_version: Mapped[str] = mapped_column(String(50), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ProductIssue id={self.id} title={self.title!r}>"

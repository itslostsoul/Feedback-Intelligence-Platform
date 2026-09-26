import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class ProductIssueSignalSnapshot(Base):
    """Deterministic aggregate signals computed from a ProductIssue's linked
    feedback (Phase 3C). One row per ProductIssue — recalculating overwrites
    this row rather than keeping a growing history, mirroring
    `FeedbackAnalysis` ("one row per item, re-analysis overwrites"), so
    rerunning the calculation is idempotent and never accumulates
    uncontrolled duplicate rows.

    Every signal here is derived purely from `product_issue_feedback`
    membership, `Feedback`, `FeedbackAnalysis`, and `FeedbackRelationship` —
    never from an LLM call — and none of it is a blended priority score. See
    `app.services.issue_signals` for the exact formulas and
    docs/architecture.md's Phase 3C section for the full definitions.
    """

    __tablename__ = "product_issue_signal_snapshot"
    __table_args__ = (
        UniqueConstraint("product_issue_id", name="uq_product_issue_signal_snapshot_product_issue_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    product_issue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_issue.id", ondelete="CASCADE"), nullable=False
    )

    # Which version of the signal-calculation logic produced this row — see
    # SIGNAL_ALGORITHM_VERSION in app.services.issue_signals. Bumped whenever
    # a formula, bucket boundary, or window definition below changes.
    signal_version: Mapped[str] = mapped_column(String(50), nullable=False)

    # The point in time recency/trend were computed relative to. Persisted
    # (rather than implied by calculated_at) so a consumer always knows
    # exactly what "latest"/"days since"/trend windows were measured against,
    # even if calculated_at drifts slightly during the DB write.
    reference_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Signal A (Frequency) — kept as a real column, not nested in JSON, since
    # it's the simplest and most commonly filtered/sorted-on signal.
    feedback_count: Mapped[int] = mapped_column(nullable=False)

    # Signals B, C, D, E, F, G, H, I, J respectively. Each is a structured
    # JSONB object (never a single scalar) so the full distribution/evidence
    # is preserved — see app.services.issue_signals for each shape.
    severity: Mapped[dict] = mapped_column(JSONB, nullable=False)
    recency: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trend: Mapped[dict] = mapped_column(JSONB, nullable=False)
    customer_reach: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_diversity: Mapped[dict] = mapped_column(JSONB, nullable=False)
    segment_coverage: Mapped[dict] = mapped_column(JSONB, nullable=False)
    duplicate_ratio: Mapped[dict] = mapped_column(JSONB, nullable=False)
    time_distribution: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evidence_quality: Mapped[dict] = mapped_column(JSONB, nullable=False)

    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ProductIssueSignalSnapshot product_issue_id={self.product_issue_id} "
            f"feedback_count={self.feedback_count}>"
        )

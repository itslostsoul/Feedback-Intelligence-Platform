import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.models.enums import MatchMethod


class DecisionIssueEvidence(Base):
    """One ProductIssue's matching provenance + classified evidence facts
    within a single DecisionEvaluation (Phase 4A).

    **Historical evidence must remain valid even if the referenced
    ProductIssueSignalSnapshot is later recalculated (overwritten, Phase 3C)
    or deleted.** `product_issue_signal_snapshot_id` is therefore only a
    "jump to the issue's *current* signals" convenience pointer
    (`ON DELETE SET NULL`) — it is never read back to reconstruct what this
    evaluation actually saw. The `supporting`/`counter`/`gaps` fact lists
    below are self-contained: each fact records its own `value` at
    evaluation time, so this row never needs to dereference the snapshot to
    remain understandable.

    `match_score` is a deterministic relevance/match-strength number (see
    app.services.decision_matching.MATCH_ALGORITHM_VERSION for its exact
    formula) — it is NOT a probability, statistical confidence, likelihood,
    or model confidence. `match_method` + `matched_terms` document exactly
    which deterministic signal(s) produced the match, so the score is never
    returned unexplained.
    """

    __tablename__ = "decision_issue_evidence"
    __table_args__ = (
        UniqueConstraint(
            "decision_evaluation_id", "product_issue_id", name="uq_decision_issue_evidence_evaluation_issue"
        ),
        Index("ix_decision_issue_evidence_decision_evaluation_id", "decision_evaluation_id"),
        Index("ix_decision_issue_evidence_product_issue_id", "product_issue_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    decision_evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decision_evaluation.id", ondelete="CASCADE"), nullable=False
    )
    product_issue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_issue.id", ondelete="CASCADE"), nullable=False
    )
    # Convenience-only pointer to the snapshot consulted at evaluation time —
    # see the class docstring. Never authoritative for historical facts.
    product_issue_signal_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_issue_signal_snapshot.id", ondelete="SET NULL"),
        nullable=True,
    )

    match_method: Mapped[MatchMethod] = mapped_column(
        Enum(MatchMethod, name="match_method", native_enum=False, validate_strings=True), nullable=False
    )
    # Deterministic relevance/match-strength score — see class docstring for
    # what it explicitly is NOT.
    match_score: Mapped[float] = mapped_column(Float, nullable=False)
    # Explainable matching provenance: which specific keyword(s) matched
    # which field(s), and whether/how the category filter applied. Always
    # populated alongside match_score — the score is never returned alone.
    matched_terms: Mapped[dict] = mapped_column(JSONB, nullable=False)

    volume_summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    supporting: Mapped[list] = mapped_column(JSONB, nullable=False)
    counter: Mapped[list] = mapped_column(JSONB, nullable=False)
    gaps: Mapped[list] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<DecisionIssueEvidence evaluation={self.decision_evaluation_id} "
            f"issue={self.product_issue_id} match_score={self.match_score}>"
        )

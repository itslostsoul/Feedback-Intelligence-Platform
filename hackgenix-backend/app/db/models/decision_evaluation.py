import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class DecisionEvaluation(Base):
    """One run of evidence evaluation for a Decision (Phase 4A).

    Deliberately **append-only** — unlike every other "recalculate" table in
    this codebase (FeedbackAnalysis, FeedbackEmbedding, ProductIssueSignalSnapshot,
    which all overwrite a single row per parent), a new row is created on
    every call to POST .../evaluate and previous rows are never touched or
    deleted. This is required, not accidental: a later phase (Decision
    Stability, not implemented here) needs to compare how a decision's
    evidence has changed across multiple points in time, which is only
    possible if history is preserved rather than overwritten.

    `decision_level_gaps` holds gaps that apply to the evaluation as a whole
    rather than to any single matched issue — e.g. `no_matching_issues_found`
    when deterministic matching (app.services.decision_matching) found no
    ProductIssue above MIN_MATCH_SCORE.
    """

    __tablename__ = "decision_evaluation"
    __table_args__ = (Index("ix_decision_evaluation_decision_id", "decision_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decision.id", ondelete="CASCADE"), nullable=False
    )

    # Which version of the matching + evidence-classification logic produced
    # this evaluation — see DECISION_EVALUATION_VERSION in
    # app.services.decision_evidence. Bumped whenever a matching rule,
    # evidence rule, or threshold changes, so a stored evaluation always
    # identifies exactly which logic produced it, independent of how
    # signal_version/clustering_version evolve afterward.
    evaluation_version: Mapped[str] = mapped_column(String(50), nullable=False)

    # The point in time this evaluation's matching/evidence were computed
    # relative to (passed through to signal-derived recency/trend facts).
    reference_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    decision_level_gaps: Mapped[list] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DecisionEvaluation id={self.id} decision_id={self.decision_id}>"

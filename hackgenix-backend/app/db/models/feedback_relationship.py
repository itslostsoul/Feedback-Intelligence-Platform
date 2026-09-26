import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.models.enums import RelationshipType


class FeedbackRelationship(Base):
    """A deterministic semantic relationship between two feedback items.

    duplicate/related/distinct are symmetric — there's no meaningful
    direction — so each unordered pair is stored exactly once:
    `feedback_id_a` is always the smaller of the two UUIDs (enforced by the
    CHECK constraint below, and by the only code path that writes these rows,
    `app.services.semantic_engine.upsert_relationship`). This is what
    prevents storing both A->B and B->A for the same pair.

    Only `duplicate` and `related` outcomes are ever written here —
    `distinct` is a valid enum member (reserved for a future phase, e.g. a
    human explicitly marking two items as unrelated to suppress
    re-surfacing) but the current semantic engine treats a `distinct`
    classification as "no relationship found" and never persists it; storing
    a row for every non-related candidate pair would grow this table
    combinatorially for no benefit.
    """

    __tablename__ = "feedback_relationship"
    __table_args__ = (
        UniqueConstraint("feedback_id_a", "feedback_id_b", name="uq_feedback_relationship_pair"),
        CheckConstraint("feedback_id_a < feedback_id_b", name="ck_feedback_relationship_ordered_pair"),
        CheckConstraint(
            "similarity_score >= 0 AND similarity_score <= 1",
            name="ck_feedback_relationship_similarity_range",
        ),
        Index("ix_feedback_relationship_feedback_id_a", "feedback_id_a"),
        Index("ix_feedback_relationship_feedback_id_b", "feedback_id_b"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    feedback_id_a: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("feedback.id", ondelete="CASCADE"), nullable=False
    )
    feedback_id_b: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("feedback.id", ondelete="CASCADE"), nullable=False
    )

    relationship_type: Mapped[RelationshipType] = mapped_column(
        Enum(RelationshipType, name="relationship_type", native_enum=False, validate_strings=True),
        nullable=False,
    )
    similarity_score: Mapped[float] = mapped_column(Float, nullable=False)

    # Identifies which version of the deterministic classification logic
    # (similarity thresholds + structured-signal rules) produced this row,
    # so a later change to those thresholds doesn't make historical rows
    # impossible to interpret. See SEMANTIC_ANALYSIS_ALGORITHM_VERSION in
    # app.services.semantic_engine.
    algorithm_version: Mapped[str] = mapped_column(String(50), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<FeedbackRelationship {self.feedback_id_a}~{self.feedback_id_b} "
            f"type={self.relationship_type}>"
        )

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.models.enums import DecisionStatus, FeedbackCategory


class Decision(Base):
    """A product question/decision to be evaluated against evidence (Phase
    4A), e.g. "Should we prioritize fixing checkout?" This row holds the
    enduring question and its deterministic matching inputs; each time it is
    evaluated (POST .../evaluate) a new, separate `DecisionEvaluation` is
    appended — see that model's docstring for why evaluations are append-only
    rather than overwritten.

    `keywords` are explicit, caller-provided deterministic tokens used for
    issue matching (see app.services.decision_matching) — deliberately never
    derived from `query_text` via an LLM or NLP extraction step, so matching
    stays fully deterministic and auditable.
    """

    __tablename__ = "decision"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[list[str]] = mapped_column(ARRAY(String(255)), nullable=False)

    category: Mapped[FeedbackCategory | None] = mapped_column(
        Enum(FeedbackCategory, name="feedback_category", native_enum=False, validate_strings=True),
        nullable=True,
    )
    affected_feature_hint: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[DecisionStatus] = mapped_column(
        Enum(DecisionStatus, name="decision_status", native_enum=False, validate_strings=True),
        nullable=False,
        default=DecisionStatus.OPEN,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Decision id={self.id} query_text={self.query_text!r}>"

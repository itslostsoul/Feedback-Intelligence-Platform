import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.models.enums import EvidenceSufficiency, GenerationStatus


class DecisionBrief(Base):
    """One grounded LLM synthesis attempt over a specific DecisionEvaluation
    (Phase 4B).

    **Deliberately append-only, and NOT unique per decision_evaluation_id**
    — regenerating creates a new row, mirroring DecisionEvaluation's own
    append-only design (Phase 4A). This is necessary, not just consistent:
    unlike every deterministic table in this codebase, LLM output is not
    guaranteed byte-identical across calls even with the same input, so
    "the brief" is never overwritten — each attempt is preserved so a past
    reader's exact wording is never silently rewritten.

    Tied to one specific `DecisionEvaluation` (never loosely to the
    `Decision`) — since `DecisionEvaluation`/`DecisionIssueEvidence` are
    already immutable once written (Phase 4A's own guarantee), this FK is
    safe to treat as authoritative for reconstructing the citation registry
    at read time; no `SET NULL` convenience-pointer pattern is needed here
    the way it was for `product_issue_signal_snapshot_id` in Phase 4A.

    `evidence_sufficiency` is computed deterministically from Phase 4A data
    BEFORE the LLM is ever called (see
    app.services.decision_brief.compute_evidence_sufficiency) and is
    persisted on both COMPLETED and FAILED rows — it is never influenced by,
    and never waits on, what the LLM writes.

    On a FAILED row, `headline`/`supporting_narrative`/`counter_narrative`/
    `gap_narrative` are all `NULL` — malformed or ungrounded LLM output is
    never persisted, only the fact that generation failed and why
    (`error_code`).
    """

    __tablename__ = "decision_brief"
    __table_args__ = (Index("ix_decision_brief_decision_evaluation_id", "decision_evaluation_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    decision_evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decision_evaluation.id", ondelete="CASCADE"), nullable=False
    )

    status: Mapped[GenerationStatus] = mapped_column(
        Enum(GenerationStatus, name="generation_status", native_enum=False, validate_strings=True),
        nullable=False,
    )
    evidence_sufficiency: Mapped[EvidenceSufficiency] = mapped_column(
        Enum(EvidenceSufficiency, name="evidence_sufficiency", native_enum=False, validate_strings=True),
        nullable=False,
    )

    headline: Mapped[str | None] = mapped_column(String, nullable=True)
    supporting_narrative: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    counter_narrative: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    gap_narrative: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Which provider/model attempted this generation, and which version of
    # the prompt + input-building + output-validation/grounding logic
    # produced it — see DECISION_BRIEF_SYNTHESIS_VERSION in
    # app.services.decision_brief. Always populated, even on failure: we
    # always know who was asked and with what logic, regardless of outcome.
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    generation_version: Mapped[str] = mapped_column(String(50), nullable=False)

    # Set only when status == FAILED — the AppError subclass name (e.g.
    # "llm_provider_error", "llm_ungrounded_output"), never the raw
    # malformed/ungrounded text itself.
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Computed in Python at generation time (datetime.now(timezone.utc)),
    # NOT read from `created_at`'s Postgres `now()` server default — two
    # briefs generated inside the same transaction would otherwise share an
    # identical `created_at` (transaction-start time), making "latest"
    # ordering ambiguous. Same fix already applied to
    # DecisionEvaluation.reference_at in Phase 4A.
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DecisionBrief id={self.id} evaluation={self.decision_evaluation_id} status={self.status}>"

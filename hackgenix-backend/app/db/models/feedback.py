import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.models.enums import ProcessingStatus, SourceType


class Feedback(Base):
    """A single raw piece of ingested feedback.

    This is the root of the traceability chain that later phases build on:
    decision -> insight -> issue -> cluster -> feedback. Everything else
    (analysis, embeddings, clustering, scoring) is derived from these rows
    and will live in separate tables added in later phases, referencing
    this one by id.
    """

    __tablename__ = "feedback"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_feedback_source_external_id"),
        Index("ix_feedback_processing_status", "processing_status"),
        Index("ix_feedback_source", "source"),
        Index("ix_feedback_ingested_at", "ingested_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Identifier of this feedback item in its originating system, if any.
    # Combined with `source`, used to detect re-ingestion of the same item.
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Origin system, e.g. "app_store", "zendesk", "csv_upload", "typeform".
    source: Mapped[str] = mapped_column(String(100), nullable=False)

    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, name="source_type", native_enum=False, validate_strings=True),
        nullable=False,
        default=SourceType.OTHER,
    )

    # Identifier of the customer who gave the feedback. May be a pseudonymous
    # or anonymous identifier when the source does not expose real identity.
    customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_tier: Mapped[str | None] = mapped_column(String(50), nullable=True)

    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # When the feedback was originally created at its source (if known),
    # as opposed to `ingested_at`, which is when *we* received it.
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    processing_status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, name="processing_status", native_enum=False, validate_strings=True),
        nullable=False,
        default=ProcessingStatus.RECEIVED,
    )

    # Arbitrary source-specific payload (e.g. rating, tags, raw source
    # fields) that doesn't warrant its own column yet. Attribute is named
    # `metadata_` because `metadata` is reserved by SQLAlchemy's
    # DeclarativeBase; the actual database column is still "metadata".
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Feedback id={self.id} source={self.source} status={self.processing_status}>"

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models.enums import ProcessingStatus, SourceType


class FeedbackCreate(BaseModel):
    """Input payload for ingesting a single feedback item.

    Normalizes safe fields (whitespace) but never invents missing data —
    a blank optional field stays `None` rather than being defaulted.
    """

    external_id: str | None = Field(default=None, max_length=255)
    source: str = Field(max_length=100)
    source_type: SourceType = SourceType.OTHER
    customer_id: str | None = Field(default=None, max_length=255)
    customer_tier: str | None = Field(default=None, max_length=50)
    raw_text: str
    language: str | None = Field(default=None, max_length=10)
    created_at: datetime | None = None
    metadata: dict | None = None

    @field_validator("source", "raw_text")
    @classmethod
    def _required_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("external_id", "customer_id", "customer_tier", "language")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class FeedbackRead(BaseModel):
    """Read-only representation of a stored feedback row."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    external_id: str | None
    source: str
    source_type: SourceType
    customer_id: str | None
    customer_tier: str | None
    raw_text: str
    language: str | None
    created_at: datetime | None
    ingested_at: datetime
    updated_at: datetime
    processing_status: ProcessingStatus
    # `Feedback.metadata` (inherited from SQLAlchemy's DeclarativeBase) is the
    # class's MetaData registry, not our column — so validation must read the
    # real attribute name `metadata_`. Only the JSON output is renamed to
    # "metadata" (serialization_alias), not the attribute lookup.
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class FeedbackListResponse(BaseModel):
    items: list[FeedbackRead]
    total: int
    limit: int
    offset: int


class BulkIngestRowError(BaseModel):
    row: int
    message: str


class BulkIngestResponse(BaseModel):
    total_rows: int
    accepted: int
    duplicates: int
    rejected: int
    created_ids: list[uuid.UUID]
    errors: list[BulkIngestRowError]

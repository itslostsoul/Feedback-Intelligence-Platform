import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models.enums import FeedbackCategory, ProcessingStatus


class FeedbackAnalysisResult(BaseModel):
    """The structured contract every LLM provider must satisfy.

    This is what gets validated against — arbitrary prose or partial JSON
    is never accepted as a successful analysis. Only per-item interpretation
    lives here; the LLM is never asked for frequency, trend, priority, or any
    other cross-feedback aggregate (those are deterministic backend
    calculations in a later phase).
    """

    model_config = ConfigDict(extra="forbid")

    category: FeedbackCategory
    affected_feature: str = Field(min_length=1, max_length=255)
    severity: int = Field(ge=1, le=10)
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1)

    @field_validator("affected_feature", "summary")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class FeedbackAnalysisRead(BaseModel):
    """API representation of a persisted analysis row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    feedback_id: uuid.UUID
    category: FeedbackCategory
    affected_feature: str
    severity: int
    confidence: float
    summary: str
    provider: str
    model: str
    analyzed_at: datetime


class AnalyzeFeedbackResponse(BaseModel):
    feedback_id: uuid.UUID
    processing_status: ProcessingStatus
    analysis: FeedbackAnalysisRead

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.enums import RelationshipType


class SimilarFeedbackItem(BaseModel):
    """One semantic-neighbor candidate. `relationship_type` is a
    classification, not proof — see docs/architecture.md: high similarity is
    a candidate-generation signal, and the deterministic refinement using
    FeedbackAnalysis fields can still downgrade it."""

    feedback_id: uuid.UUID
    # Cosine similarity's true range is [-1, 1]; real text embeddings are
    # almost always positive, but the field isn't artificially narrowed.
    similarity: float = Field(ge=-1.0, le=1.0)
    relationship_type: RelationshipType


class SimilarFeedbackResponse(BaseModel):
    feedback_id: uuid.UUID
    algorithm_version: str
    candidates: list[SimilarFeedbackItem]


class FeedbackRelationshipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    feedback_id_a: uuid.UUID
    feedback_id_b: uuid.UUID
    relationship_type: RelationshipType
    similarity_score: float
    algorithm_version: str
    created_at: datetime
    updated_at: datetime


class SemanticAnalysisResponse(BaseModel):
    feedback_id: uuid.UUID
    algorithm_version: str
    candidates_considered: int
    relationships: list[FeedbackRelationshipRead]

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.db.models.enums import FeedbackCategory, ProductIssueStatus


class ProductIssueRead(BaseModel):
    """`feedback_count` is deterministically derived from
    `product_issue_feedback` at query time — it is never a stored column
    (see ProductIssue's docstring: this is an evidence container, not a
    place for computed aggregates)."""

    id: uuid.UUID
    canonical_feedback_id: uuid.UUID
    title: str
    category: FeedbackCategory | None
    affected_feature: str | None
    status: ProductIssueStatus
    clustering_version: str
    feedback_count: int
    created_at: datetime
    updated_at: datetime


class ProductIssueDetail(ProductIssueRead):
    feedback_ids: list[uuid.UUID]


class ProductIssueListResponse(BaseModel):
    items: list[ProductIssueRead]
    total: int
    limit: int
    offset: int


class IssueDiscoveryResponse(BaseModel):
    clustering_version: str
    components_considered: int
    issues_created: int
    issues_updated: int
    feedback_linked: int
    feedback_left_unclustered: int

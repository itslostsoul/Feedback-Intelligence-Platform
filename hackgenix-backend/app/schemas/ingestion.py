from typing import Literal

from pydantic import BaseModel

from app.schemas.feedback import FeedbackRead


class WebhookIngestResponse(BaseModel):
    """Response for POST /ingestion/webhook/{source}. `status` is explicit
    in the body (rather than relying on the caller inspecting the HTTP
    status code) so external systems and the demo UI can branch on it
    directly."""

    status: Literal["created", "duplicate"]
    feedback: FeedbackRead


class SyncResultResponse(BaseModel):
    """Response for POST /ingestion/sync/{source} — mirrors the same
    total/accepted/duplicates/rejected shape as BulkIngestResponse so both
    "batch ingestion" surfaces communicate results the same way."""

    source: str
    records_fetched: int
    new: int
    duplicates: int
    failed: int
    errors: list[str] = []

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.feedback import FeedbackRead
from app.schemas.ingestion import SyncResultResponse, WebhookIngestResponse
from app.services.feedback_ingestion import create_or_get_feedback
from app.services.feedback_sync import run_sync
from app.services.webhook_normalization import normalize_webhook_payload

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


@router.post("/webhook/{source}", response_model=WebhookIngestResponse)
def webhook_ingest_endpoint(source: str, payload: dict, db: Session = Depends(get_db)) -> WebhookIngestResponse:
    """Live event receiver for external feedback providers — the
    integration point a company points its support desk, survey tool, or
    app store review feed at, instead of exporting and re-uploading data by
    hand. Configure that platform's own webhook/notification setting to
    POST each new event here as it happens.

    Payload shape is provider-specific — this endpoint normalizes it into
    the same input shape used by REST/CSV/JSON ingestion (FeedbackCreate),
    then calls the exact same common ingestion service, so webhook-sourced
    feedback is deduplicated by (source, external_id) identically to every
    other channel. `source` becomes both the URL path segment and the
    stored `Feedback.source`.

    Supported demo sources: `zendesk`, `survey`, `app_store`, `generic`
    (use `generic` for any provider without a dedicated normalizer yet).
    An unrecognized source returns 404 (`ingestion_source_not_supported`);
    a structurally valid but incomplete payload (e.g. missing the review
    text) returns 422 (`validation_error`).
    """
    feedback_create = normalize_webhook_payload(source, payload)
    feedback, was_duplicate = create_or_get_feedback(
        db, feedback_create, extra_metadata={"ingestion_method": "webhook", "provider": source}
    )
    return WebhookIngestResponse(
        status="duplicate" if was_duplicate else "created",
        feedback=FeedbackRead.model_validate(feedback),
    )


@router.post("/sync/{source}", response_model=SyncResultResponse)
def sync_ingest_endpoint(source: str, db: Session = Depends(get_db)) -> SyncResultResponse:
    """Manually triggers one pull from a registered FeedbackSourceConnector
    — for a company's system that has no webhook support at all, so
    SignalForge checks it for new feedback instead of waiting to be told.
    Never runs on a background schedule/timer inside this app; a real
    deployment points an external cron job (or a scheduler like GitHub
    Actions / Cloud Scheduler) at this same endpoint on an interval. Safe
    to call repeatedly — records already seen (by source + external_id)
    come back as duplicates, never re-created.

    Supported demo sources: `demo` (simulated partner REST API), `csv_drop`
    (simulated shared CSV drop location). An unrecognized source returns
    404; an upstream connector failure returns 502
    (`ingestion_connector_error`).
    """
    return run_sync(db, source)

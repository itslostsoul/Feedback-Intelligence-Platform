"""FeedbackSyncService — a lightweight, manually/externally-triggered
polling ingestion path for sources that don't support webhooks.

Deliberately NOT a background scheduler: nothing here starts a thread, a
timer, or a task queue when the app boots (that would misbehave under
multiple uvicorn/gunicorn workers and complicate the dev server). Instead
`run_sync` is plain, synchronous, idempotent-to-call code reachable at
POST /api/v1/ingestion/sync/{source} — a real deployment points an external
cron job (or a scheduler like GitHub Actions/Cloud Scheduler) at that same
endpoint rather than needing new infrastructure here.
"""

import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import IngestionConnectorError, IngestionSourceNotSupportedError
from app.schemas.ingestion import SyncResultResponse
from app.services.connectors import CONNECTOR_REGISTRY
from app.services.connectors.base import ConnectorError
from app.services.feedback_ingestion import create_or_get_feedback

logger = logging.getLogger(__name__)


def run_sync(db: Session, source: str) -> SyncResultResponse:
    connector = CONNECTOR_REGISTRY.get(source)
    if connector is None:
        raise IngestionSourceNotSupportedError(
            f"No sync connector registered for source '{source}'. "
            f"Supported: {', '.join(sorted(CONNECTOR_REGISTRY))}."
        )

    try:
        records = connector.fetch_feedback()
    except ConnectorError as exc:
        raise IngestionConnectorError(f"Connector '{source}' failed: {exc}") from exc

    new_count = 0
    duplicate_count = 0
    failed_count = 0
    errors: list[str] = []

    for record in records:
        try:
            _, was_duplicate = create_or_get_feedback(
                db, record, extra_metadata={"ingestion_method": "scheduled_sync", "connector": source}
            )
        except SQLAlchemyError as exc:
            db.rollback()
            failed_count += 1
            errors.append(str(exc))
            continue

        if was_duplicate:
            duplicate_count += 1
        else:
            new_count += 1

    logger.info(
        "feedback_sync source=%s fetched=%s new=%s duplicates=%s failed=%s",
        source,
        len(records),
        new_count,
        duplicate_count,
        failed_count,
    )

    return SyncResultResponse(
        source=source,
        records_fetched=len(records),
        new=new_count,
        duplicates=duplicate_count,
        failed=failed_count,
        errors=errors,
    )

import csv
import io
import logging

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ValidationAppError
from app.db.models.enums import ProcessingStatus
from app.db.models.feedback import Feedback
from app.schemas.feedback import BulkIngestResponse, BulkIngestRowError, FeedbackCreate

logger = logging.getLogger(__name__)

# The `feedback` table's uq_feedback_source_external_id constraint is what
# idempotency relies on. Postgres treats NULL != NULL for unique constraints,
# so rows ingested without an external_id can never be deduplicated by the
# database and will always create a new row — this is a known limitation,
# not a bug (see docs/architecture.md).
REQUIRED_CSV_COLUMNS = {"source", "raw_text"}
OPTIONAL_CSV_COLUMNS = {
    "external_id",
    "source_type",
    "customer_id",
    "customer_tier",
    "language",
    "created_at",
}
MAX_BULK_ROWS = 5000


def find_existing(db: Session, source: str, external_id: str | None) -> Feedback | None:
    if external_id is None:
        return None
    stmt = select(Feedback).where(Feedback.source == source, Feedback.external_id == external_id)
    return db.execute(stmt).scalar_one_or_none()


def _build_feedback(payload: FeedbackCreate) -> Feedback:
    return Feedback(
        external_id=payload.external_id,
        source=payload.source,
        source_type=payload.source_type,
        customer_id=payload.customer_id,
        customer_tier=payload.customer_tier,
        raw_text=payload.raw_text,
        language=payload.language,
        created_at=payload.created_at,
        processing_status=ProcessingStatus.RECEIVED,
        metadata_=payload.metadata,
    )


def create_or_get_feedback(db: Session, payload: FeedbackCreate) -> tuple[Feedback, bool]:
    """Insert a new feedback row, or return the existing one if (source,
    external_id) already matches a row. Returns (feedback, was_duplicate).

    Never logs `raw_text`, `customer_id`, or `metadata` — only identifiers
    and the outcome.
    """
    existing = find_existing(db, payload.source, payload.external_id)
    if existing is not None:
        logger.info("feedback_duplicate id=%s source=%s", existing.id, existing.source)
        return existing, True

    feedback = _build_feedback(payload)
    db.add(feedback)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = find_existing(db, payload.source, payload.external_id)
        if existing is not None:
            logger.info("feedback_duplicate_race id=%s source=%s", existing.id, existing.source)
            return existing, True
        raise
    db.refresh(feedback)
    logger.info("feedback_ingested id=%s source=%s", feedback.id, feedback.source)
    return feedback, False


def _clean(row: dict[str, str], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _row_to_create_kwargs(row: dict[str, str]) -> dict:
    kwargs: dict = {"source": _clean(row, "source") or "", "raw_text": _clean(row, "raw_text") or ""}
    for key in ("external_id", "source_type", "customer_id", "customer_tier", "language", "created_at"):
        value = _clean(row, key)
        if value is not None:
            kwargs[key] = value
    return kwargs


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


def parse_and_ingest_csv(db: Session, raw_bytes: bytes) -> BulkIngestResponse:
    """Parse a CSV file and ingest each row, never letting a single bad row
    abort the whole import. Structural problems (empty file, missing
    required columns, too many rows) reject the whole request instead,
    since there is nothing safe to partially import in that case.
    """
    if not raw_bytes.strip():
        raise ValidationAppError("CSV file is empty.")

    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationAppError("CSV file must be UTF-8 encoded.") from exc

    reader = csv.DictReader(io.StringIO(text))
    fieldnames = {(name or "").strip() for name in (reader.fieldnames or [])}
    missing = REQUIRED_CSV_COLUMNS - fieldnames
    if missing:
        raise ValidationAppError(
            f"CSV is missing required column(s): {', '.join(sorted(missing))}."
        )

    rows = list(reader)
    if len(rows) > MAX_BULK_ROWS:
        raise ValidationAppError(f"CSV exceeds the maximum of {MAX_BULK_ROWS} rows per import.")

    accepted = 0
    duplicates = 0
    rejected = 0
    created_ids: list = []
    errors: list[BulkIngestRowError] = []
    seen_keys: set[tuple[str, str]] = set()

    for index, row in enumerate(rows, start=2):  # row 1 is the header
        try:
            payload = FeedbackCreate(**_row_to_create_kwargs(row))
        except ValidationError as exc:
            rejected += 1
            errors.append(BulkIngestRowError(row=index, message=_format_validation_error(exc)))
            continue

        dedup_key = (payload.source, payload.external_id) if payload.external_id else None
        if dedup_key is not None and dedup_key in seen_keys:
            duplicates += 1
            continue

        feedback, was_duplicate = create_or_get_feedback(db, payload)
        if dedup_key is not None:
            seen_keys.add(dedup_key)

        if was_duplicate:
            duplicates += 1
        else:
            accepted += 1
            created_ids.append(feedback.id)

    logger.info(
        "feedback_bulk_ingest total=%s accepted=%s duplicates=%s rejected=%s",
        len(rows),
        accepted,
        duplicates,
        rejected,
    )

    return BulkIngestResponse(
        total_rows=len(rows),
        accepted=accepted,
        duplicates=duplicates,
        rejected=rejected,
        created_ids=created_ids,
        errors=errors,
    )

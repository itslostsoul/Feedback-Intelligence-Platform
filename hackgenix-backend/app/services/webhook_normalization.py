"""Normalizes provider-specific webhook payloads into the same
`FeedbackCreate` shape used by REST and CSV ingestion, so webhook-sourced
feedback converges on the exact same common ingestion service
(`create_or_get_feedback`) — and is therefore deduplicated identically.

Deliberately NOT a generic/pluggable provider framework: each supported
source is one small function mapping its realistic payload shape onto
FeedbackCreate. Adding a fifth demo source means adding one function and one
registry entry, not a new abstraction layer.
"""

from datetime import datetime

from app.core.errors import IngestionSourceNotSupportedError, ValidationAppError
from app.db.models.enums import SourceType
from app.schemas.feedback import FeedbackCreate

SUPPORTED_WEBHOOK_SOURCES = frozenset({"zendesk", "survey", "app_store", "generic"})


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationAppError(f"Webhook payload is missing required field '{field}'.")
    return value.strip()


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _normalize_zendesk(payload: dict) -> FeedbackCreate:
    """Zendesk-style support ticket webhook, e.g.:
    {"id": 91827, "requester": {"id": "C1029", "tier": "premium"},
     "subject": "...", "description": "...", "created_at": "..."}
    """
    requester = payload.get("requester") if isinstance(payload.get("requester"), dict) else {}
    text = payload.get("description") or payload.get("subject")
    return FeedbackCreate(
        external_id=_optional_str(payload.get("id")),
        source="zendesk",
        source_type=SourceType.SUPPORT_TICKET,
        customer_id=_optional_str(requester.get("id")),
        customer_tier=_optional_str(requester.get("tier")),
        raw_text=_require_text(text, "description"),
        created_at=_parse_timestamp(payload.get("created_at")),
    )


def _normalize_survey(payload: dict) -> FeedbackCreate:
    """Survey-response-style webhook, e.g.:
    {"response_id": "resp_4471", "respondent_id": "C2044",
     "answer_text": "...", "submitted_at": "..."}
    """
    return FeedbackCreate(
        external_id=_optional_str(payload.get("response_id")),
        source="survey",
        source_type=SourceType.SURVEY,
        customer_id=_optional_str(payload.get("respondent_id")),
        raw_text=_require_text(payload.get("answer_text"), "answer_text"),
        created_at=_parse_timestamp(payload.get("submitted_at")),
    )


def _normalize_app_store(payload: dict) -> FeedbackCreate:
    """App store review webhook, e.g.:
    {"review_id": "rev_7788", "author": "user123", "rating": 2,
     "review_text": "...", "date": "..."}
    """
    rating = payload.get("rating")
    return FeedbackCreate(
        external_id=_optional_str(payload.get("review_id")),
        source="app_store",
        source_type=SourceType.REVIEW,
        customer_id=_optional_str(payload.get("author")),
        raw_text=_require_text(payload.get("review_text"), "review_text"),
        created_at=_parse_timestamp(payload.get("date")),
        metadata={"rating": rating} if rating is not None else None,
    )


def _normalize_generic(payload: dict) -> FeedbackCreate:
    """Fallback shape for any provider without a dedicated normalizer yet,
    e.g.: {"event_id": "evt_1001", "customer": {"id": "C100"},
    "text": "...", "timestamp": "..."}
    """
    customer = payload.get("customer") if isinstance(payload.get("customer"), dict) else {}
    return FeedbackCreate(
        external_id=_optional_str(payload.get("event_id")),
        source="generic",
        source_type=SourceType.OTHER,
        customer_id=_optional_str(customer.get("id")),
        raw_text=_require_text(payload.get("text"), "text"),
        created_at=_parse_timestamp(payload.get("timestamp")),
    )


_NORMALIZERS = {
    "zendesk": _normalize_zendesk,
    "survey": _normalize_survey,
    "app_store": _normalize_app_store,
    "generic": _normalize_generic,
}


def normalize_webhook_payload(source: str, payload: dict) -> FeedbackCreate:
    normalizer = _NORMALIZERS.get(source)
    if normalizer is None:
        raise IngestionSourceNotSupportedError(
            f"Unsupported webhook source '{source}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_WEBHOOK_SOURCES))}."
        )
    if not isinstance(payload, dict):
        raise ValidationAppError("Webhook payload must be a JSON object.")
    return normalizer(payload)

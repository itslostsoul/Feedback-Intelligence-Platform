from abc import ABC, abstractmethod
from datetime import datetime

from app.schemas.feedback import FeedbackCreate


class ConnectorError(Exception):
    """Raised when a FeedbackSourceConnector cannot reach or read its
    upstream source. Caught by FeedbackSyncService and surfaced as
    IngestionConnectorError (502) rather than a raw 500, so a sync failure
    reads as an integration problem, not an internal SignalForge bug."""


class FeedbackSourceConnector(ABC):
    """A pollable feedback source that doesn't support (or isn't wired to)
    webhooks — the abstraction behind scheduled/manual sync
    (POST /api/v1/ingestion/sync/{source}).

    `fetch_feedback` returns already-normalized, already-validated
    FeedbackCreate records, ready for the exact same common ingestion
    service (`create_or_get_feedback`) used by REST, CSV, and webhook
    ingestion — so a synced record is deduplicated identically to every
    other channel. Implementations should raise `ConnectorError`, never a
    bare exception, for any upstream failure (network down, auth failure,
    malformed response) so the sync endpoint can report it distinctly from
    a per-record validation problem.
    """

    #: Registry key / the value stored in Feedback.source for records this
    #: connector produces.
    source: str

    @abstractmethod
    def fetch_feedback(self, since: datetime | None = None) -> list[FeedbackCreate]:
        """Returns new/updated records since `since` (or "everything it
        currently has" the first time, when `since` is None).

        A real connector would track a high-water mark (e.g. the latest
        `created_at` it has already fetched) in its own external system or
        a small local table; the bundled demo connectors return a small
        fixed/synthetic set on every call so re-running the sync
        predictably demonstrates deduplication (first run: all new; second
        run: all duplicates).
        """
        raise NotImplementedError

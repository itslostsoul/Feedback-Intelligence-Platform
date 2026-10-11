import csv
import io
from datetime import datetime, timezone

from app.db.models.enums import SourceType
from app.schemas.feedback import FeedbackCreate
from app.services.connectors.base import FeedbackSourceConnector
from app.services.feedback_ingestion import _row_to_create_kwargs


class DemoAPIConnector(FeedbackSourceConnector):
    """Simulates polling a partner's REST API (e.g. a support platform that
    has no webhook support and must be checked periodically instead).
    Returns the same fixed set of synthetic records on every call — a real
    connector would call out over HTTP and track a high-water mark, but for
    a demo this is what makes running the sync twice show "all new" then
    "all duplicates" without any external system to set up.
    """

    source = "demo"

    _RECORDS: list[dict] = [
        {
            "external_id": "demo-api-1001",
            "customer_id": "C-9001",
            "customer_tier": "premium",
            "raw_text": "Export to PDF has been failing since the last update.",
            "source_type": SourceType.SUPPORT_TICKET,
        },
        {
            "external_id": "demo-api-1002",
            "customer_id": "C-9002",
            "customer_tier": "standard",
            "raw_text": "Would love a dark mode option in the settings page.",
            "source_type": SourceType.OTHER,
        },
        {
            "external_id": "demo-api-1003",
            "customer_id": "C-9003",
            "customer_tier": "standard",
            "raw_text": "The mobile app logs me out every few hours, very annoying.",
            "source_type": SourceType.SUPPORT_TICKET,
        },
    ]

    def fetch_feedback(self, since: datetime | None = None) -> list[FeedbackCreate]:
        now = datetime.now(timezone.utc)
        return [FeedbackCreate(source=self.source, created_at=now, **record) for record in self._RECORDS]


class DemoCSVConnector(FeedbackSourceConnector):
    """Simulates polling a shared CSV drop location (e.g. a nightly export
    a partner uploads to a shared drive/SFTP instead of calling an API).
    Reuses the same row-parsing helper as `POST /feedback/bulk` so a CSV
    row means the same thing whether a person uploads it or a connector
    fetches it."""

    source = "csv_drop"

    _CSV_TEXT = (
        "external_id,customer_id,customer_tier,raw_text,source_type\n"
        "demo-csv-2001,C-8001,standard,Search results are missing recent items.,other\n"
        "demo-csv-2002,C-8002,premium,Billing page shows the wrong currency for EU accounts.,support_ticket\n"
    )

    def fetch_feedback(self, since: datetime | None = None) -> list[FeedbackCreate]:
        reader = csv.DictReader(io.StringIO(self._CSV_TEXT))
        records = []
        for row in reader:
            kwargs = _row_to_create_kwargs(row)
            kwargs["source"] = self.source
            records.append(FeedbackCreate(**kwargs))
        return records

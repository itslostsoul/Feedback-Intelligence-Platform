"""Tests for the webhook and scheduled/polling ingestion channels (Phase:
"all four ingestion methods"). REST ingestion is covered by
test_feedback.py and CSV bulk ingestion by test_feedback_bulk.py — both
already exercise the same common ingestion service these tests target from
the other two directions.
"""

from app.services.connectors import CONNECTOR_REGISTRY
from app.services.connectors.base import ConnectorError, FeedbackSourceConnector

# ---------------------------------------------------------------------------
# Webhook ingestion
# ---------------------------------------------------------------------------


def test_webhook_generic_valid_payload_creates_feedback(client) -> None:
    response = client.post(
        "/api/v1/ingestion/webhook/generic",
        json={
            "event_id": "evt_test_1",
            "customer": {"id": "C100"},
            "text": "Checkout keeps failing.",
            "timestamp": "2026-09-25T10:00:00Z",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "created"
    feedback = body["feedback"]
    assert feedback["source"] == "generic"
    assert feedback["source_type"] == "other"
    assert feedback["external_id"] == "evt_test_1"
    assert feedback["customer_id"] == "C100"
    assert feedback["raw_text"] == "Checkout keeps failing."
    assert feedback["metadata"]["ingestion_method"] == "webhook"
    assert feedback["metadata"]["provider"] == "generic"


def test_webhook_zendesk_valid_payload_normalizes_fields(client) -> None:
    response = client.post(
        "/api/v1/ingestion/webhook/zendesk",
        json={
            "id": 91827,
            "requester": {"id": "C1029", "tier": "premium"},
            "subject": "Card declined",
            "description": "My international card keeps getting declined.",
            "created_at": "2026-09-25T10:30:00Z",
        },
    )

    assert response.status_code == 200
    feedback = response.json()["feedback"]
    assert feedback["source"] == "zendesk"
    assert feedback["source_type"] == "support_ticket"
    assert feedback["external_id"] == "91827"
    assert feedback["customer_id"] == "C1029"
    assert feedback["customer_tier"] == "premium"
    assert feedback["raw_text"] == "My international card keeps getting declined."


def test_webhook_survey_valid_payload(client) -> None:
    response = client.post(
        "/api/v1/ingestion/webhook/survey",
        json={
            "response_id": "resp_1",
            "respondent_id": "C500",
            "answer_text": "Onboarding was confusing, took three tries.",
            "submitted_at": "2026-09-20T09:00:00Z",
        },
    )

    assert response.status_code == 200
    feedback = response.json()["feedback"]
    assert feedback["source"] == "survey"
    assert feedback["source_type"] == "survey"
    assert feedback["external_id"] == "resp_1"
    assert feedback["customer_id"] == "C500"


def test_webhook_app_store_valid_payload(client) -> None:
    response = client.post(
        "/api/v1/ingestion/webhook/app_store",
        json={
            "review_id": "rev_1",
            "author": "user123",
            "rating": 2,
            "review_text": "Crashes constantly on launch.",
            "date": "2026-09-18T00:00:00Z",
        },
    )

    assert response.status_code == 200
    feedback = response.json()["feedback"]
    assert feedback["source"] == "app_store"
    assert feedback["source_type"] == "review"
    assert feedback["external_id"] == "rev_1"
    assert feedback["metadata"]["rating"] == 2
    assert feedback["metadata"]["ingestion_method"] == "webhook"


def test_webhook_invalid_payload_missing_required_field(client) -> None:
    response = client.post(
        "/api/v1/ingestion/webhook/generic",
        json={"event_id": "evt_bad", "customer": {"id": "C1"}},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_webhook_duplicate_event_is_deduplicated(client) -> None:
    payload = {
        "event_id": "evt_dup_1",
        "customer": {"id": "C1"},
        "text": "Same event sent twice.",
        "timestamp": "2026-09-25T10:00:00Z",
    }

    first = client.post("/api/v1/ingestion/webhook/generic", json=payload)
    second = client.post("/api/v1/ingestion/webhook/generic", json=payload)

    assert first.json()["status"] == "created"
    assert second.json()["status"] == "duplicate"
    assert first.json()["feedback"]["id"] == second.json()["feedback"]["id"]


def test_webhook_unsupported_source_is_rejected(client) -> None:
    response = client.post(
        "/api/v1/ingestion/webhook/unknown_provider",
        json={"text": "hi"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ingestion_source_not_supported"


def test_webhook_uses_same_dedup_as_rest_ingestion(client) -> None:
    client.post(
        "/api/v1/feedback",
        json={"source": "zendesk", "external_id": "shared-1", "raw_text": "Original ticket."},
    )

    response = client.post(
        "/api/v1/ingestion/webhook/zendesk",
        json={"id": "shared-1", "description": "Resent via webhook."},
    )

    assert response.json()["status"] == "duplicate"


# ---------------------------------------------------------------------------
# Scheduled / polling ingestion
# ---------------------------------------------------------------------------


def test_sync_demo_connector_ingests_new_records(client) -> None:
    response = client.post("/api/v1/ingestion/sync/demo")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "demo"
    assert body["records_fetched"] == 3
    assert body["new"] == 3
    assert body["duplicates"] == 0
    assert body["failed"] == 0


def test_sync_demo_connector_is_idempotent_on_rerun(client) -> None:
    client.post("/api/v1/ingestion/sync/demo")

    response = client.post("/api/v1/ingestion/sync/demo")

    body = response.json()
    assert body["new"] == 0
    assert body["duplicates"] == 3


def test_sync_csv_drop_connector_ingests_new_records(client) -> None:
    response = client.post("/api/v1/ingestion/sync/csv_drop")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "csv_drop"
    assert body["records_fetched"] == 2
    assert body["new"] == 2


def test_sync_uses_same_dedup_as_other_ingestion_paths(client) -> None:
    client.post(
        "/api/v1/feedback",
        json={"source": "demo", "external_id": "demo-api-1001", "raw_text": "Already ingested manually."},
    )

    response = client.post("/api/v1/ingestion/sync/demo")

    body = response.json()
    assert body["duplicates"] == 1
    assert body["new"] == 2


def test_sync_unsupported_source_is_rejected(client) -> None:
    response = client.post("/api/v1/ingestion/sync/not_a_real_source")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ingestion_source_not_supported"


def test_sync_connector_failure_is_handled(client, monkeypatch) -> None:
    class FailingConnector(FeedbackSourceConnector):
        source = "failing"

        def fetch_feedback(self, since=None):
            raise ConnectorError("upstream API unreachable")

    monkeypatch.setitem(CONNECTOR_REGISTRY, "failing", FailingConnector())

    response = client.post("/api/v1/ingestion/sync/failing")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "ingestion_connector_error"

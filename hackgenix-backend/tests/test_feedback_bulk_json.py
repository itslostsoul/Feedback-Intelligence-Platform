import io
import json

from app.db.models.enums import ProcessingStatus


def _json_file(data, filename: str = "feedback.json"):
    content = data if isinstance(data, (str, bytes)) else json.dumps(data)
    if isinstance(content, str):
        content = content.encode("utf-8")
    return {"file": (filename, io.BytesIO(content), "application/json")}


def test_bulk_json_valid_array_import(client) -> None:
    records = [
        {
            "external_id": "review_001",
            "source": "app_store",
            "source_type": "review",
            "customer_id": "C101",
            "customer_tier": "premium",
            "raw_text": "My international card keeps getting declined.",
            "created_at": "2026-09-25T10:30:00Z",
        },
        {
            "external_id": "review_002",
            "source": "app_store",
            "source_type": "review",
            "customer_id": "C102",
            "customer_tier": "free",
            "raw_text": "The checkout interface is confusing.",
            "created_at": "2026-09-25T10:32:00Z",
        },
    ]

    response = client.post("/api/v1/feedback/bulk/json", files=_json_file(records))

    assert response.status_code == 200
    body = response.json()
    assert body["total_rows"] == 2
    assert body["accepted"] == 2
    assert body["duplicates"] == 0
    assert body["rejected"] == 0
    assert body["errors"] == []
    assert len(body["created_ids"]) == 2

    fetched = client.get(f"/api/v1/feedback/{body['created_ids'][0]}").json()
    assert fetched["processing_status"] == ProcessingStatus.RECEIVED.value
    assert fetched["metadata"]["ingestion_method"] == "json"


def test_bulk_json_multiple_records_all_fields(client) -> None:
    records = [{"source": "survey", "raw_text": f"Feedback number {i}"} for i in range(25)]

    response = client.post("/api/v1/feedback/bulk/json", files=_json_file(records))

    body = response.json()
    assert body["total_rows"] == 25
    assert body["accepted"] == 25


def test_bulk_json_rejects_invalid_records_without_aborting_import(client) -> None:
    records = [
        {"source": "app_store", "raw_text": "This one is fine"},
        {"source": "", "raw_text": "No source given here"},
        {"source": "app_store", "raw_text": "   "},
        {"source": "app_store", "raw_text": "Another good record"},
    ]

    response = client.post("/api/v1/feedback/bulk/json", files=_json_file(records))

    assert response.status_code == 200
    body = response.json()
    assert body["total_rows"] == 4
    assert body["accepted"] == 2
    assert body["rejected"] == 2
    assert len(body["errors"]) == 2
    rows_with_errors = {err["row"] for err in body["errors"]}
    assert rows_with_errors == {2, 3}  # 1-indexed position in the array
    for err in body["errors"]:
        assert isinstance(err["message"], str) and err["message"]


def test_bulk_json_rejects_non_object_record(client) -> None:
    records = [{"source": "app_store", "raw_text": "Fine"}, "not an object", 42]

    response = client.post("/api/v1/feedback/bulk/json", files=_json_file(records))

    body = response.json()
    assert body["total_rows"] == 3
    assert body["accepted"] == 1
    assert body["rejected"] == 2


def test_bulk_json_deduplicates_within_the_same_file(client) -> None:
    records = [
        {"source": "app_store", "external_id": "dup-1", "raw_text": "First copy"},
        {"source": "app_store", "external_id": "dup-1", "raw_text": "Second copy of the same item"},
    ]

    response = client.post("/api/v1/feedback/bulk/json", files=_json_file(records))

    body = response.json()
    assert body["total_rows"] == 2
    assert body["accepted"] == 1
    assert body["duplicates"] == 1
    assert body["rejected"] == 0


def test_bulk_json_deduplicates_against_existing_row(client) -> None:
    client.post(
        "/api/v1/feedback",
        json={"source": "app_store", "external_id": "pre-existing", "raw_text": "Already here"},
    )

    records = [{"source": "app_store", "external_id": "pre-existing", "raw_text": "Resent import"}]
    response = client.post("/api/v1/feedback/bulk/json", files=_json_file(records))

    body = response.json()
    assert body["accepted"] == 0
    assert body["duplicates"] == 1


def test_bulk_json_uses_same_dedup_as_rest_and_csv(client) -> None:
    """CSV, JSON, and REST must all agree on what counts as a duplicate."""
    client.post(
        "/api/v1/feedback/bulk",
        files={"file": ("f.csv", io.BytesIO(b"source,external_id,raw_text\nzendesk,shared-x,Via CSV\n"), "text/csv")},
    )

    records = [{"source": "zendesk", "external_id": "shared-x", "raw_text": "Via JSON"}]
    response = client.post("/api/v1/feedback/bulk/json", files=_json_file(records))

    assert response.json()["duplicates"] == 1
    assert response.json()["accepted"] == 0


def test_bulk_json_empty_array_is_a_no_op_success(client) -> None:
    response = client.post("/api/v1/feedback/bulk/json", files=_json_file([]))

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "total_rows": 0,
        "accepted": 0,
        "duplicates": 0,
        "rejected": 0,
        "created_ids": [],
        "errors": [],
    }


def test_bulk_json_empty_file_is_rejected(client) -> None:
    response = client.post("/api/v1/feedback/bulk/json", files=_json_file(""))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_bulk_json_malformed_json_is_rejected(client) -> None:
    response = client.post(
        "/api/v1/feedback/bulk/json",
        files=_json_file(b"{not valid json at all"),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_bulk_json_top_level_object_instead_of_array_is_rejected(client) -> None:
    response = client.post(
        "/api/v1/feedback/bulk/json",
        files=_json_file({"source": "app_store", "raw_text": "I'm an object, not an array"}),
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert "array" in body["error"]["message"].lower()


def test_bulk_json_exceeding_max_rows_is_rejected(client, monkeypatch) -> None:
    import app.services.feedback_ingestion as ingestion_module

    monkeypatch.setattr(ingestion_module, "MAX_BULK_ROWS", 3)
    records = [{"source": "app_store", "raw_text": f"item {i}"} for i in range(4)]

    response = client.post("/api/v1/feedback/bulk/json", files=_json_file(records))

    assert response.status_code == 422
    assert "maximum" in response.json()["error"]["message"].lower()

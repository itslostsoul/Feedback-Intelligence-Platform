import io

from app.db.models.enums import ProcessingStatus


def _csv_file(content: str, filename: str = "feedback.csv"):
    return {"file": (filename, io.BytesIO(content.encode("utf-8")), "text/csv")}


def test_bulk_csv_valid_import(client) -> None:
    csv_content = (
        "source,external_id,raw_text,source_type\n"
        "app_store,bulk-1,Great app but slow on startup,review\n"
        "app_store,bulk-2,Crashes on iPad,review\n"
        "zendesk,bulk-3,Refund not processed,support_ticket\n"
    )

    response = client.post("/api/v1/feedback/bulk", files=_csv_file(csv_content))

    assert response.status_code == 200
    body = response.json()
    assert body["total_rows"] == 3
    assert body["accepted"] == 3
    assert body["duplicates"] == 0
    assert body["rejected"] == 0
    assert body["errors"] == []
    assert len(body["created_ids"]) == 3

    fetched = client.get(f"/api/v1/feedback/{body['created_ids'][0]}").json()
    assert fetched["processing_status"] == ProcessingStatus.RECEIVED.value


def test_bulk_csv_rejects_invalid_rows_without_aborting_import(client) -> None:
    csv_content = (
        "source,external_id,raw_text\n"
        "app_store,ok-1,This one is fine\n"
        ",missing-source,No source given here\n"
        "app_store,blank-text,   \n"
        "app_store,ok-2,Another good row\n"
    )

    response = client.post("/api/v1/feedback/bulk", files=_csv_file(csv_content))

    assert response.status_code == 200
    body = response.json()
    assert body["total_rows"] == 4
    assert body["accepted"] == 2
    assert body["rejected"] == 2
    assert len(body["errors"]) == 2
    rows_with_errors = {err["row"] for err in body["errors"]}
    assert rows_with_errors == {3, 4}  # header is row 1, data starts at row 2
    for err in body["errors"]:
        assert isinstance(err["message"], str) and err["message"]


def test_bulk_csv_deduplicates_within_the_same_file(client) -> None:
    csv_content = (
        "source,external_id,raw_text\n"
        "app_store,dup-1,First copy\n"
        "app_store,dup-1,Second copy of the same item\n"
    )

    response = client.post("/api/v1/feedback/bulk", files=_csv_file(csv_content))

    body = response.json()
    assert body["total_rows"] == 2
    assert body["accepted"] == 1
    assert body["duplicates"] == 1
    assert body["rejected"] == 0


def test_bulk_csv_deduplicates_against_existing_row(client) -> None:
    client.post(
        "/api/v1/feedback",
        json={"source": "app_store", "external_id": "pre-existing", "raw_text": "Already here"},
    )

    csv_content = "source,external_id,raw_text\napp_store,pre-existing,Resent import\n"
    response = client.post("/api/v1/feedback/bulk", files=_csv_file(csv_content))

    body = response.json()
    assert body["accepted"] == 0
    assert body["duplicates"] == 1


def test_bulk_csv_header_only_is_a_no_op_success(client) -> None:
    response = client.post("/api/v1/feedback/bulk", files=_csv_file("source,raw_text\n"))

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


def test_bulk_csv_empty_file_is_rejected(client) -> None:
    response = client.post("/api/v1/feedback/bulk", files=_csv_file(""))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_bulk_csv_missing_required_columns_is_rejected(client) -> None:
    response = client.post(
        "/api/v1/feedback/bulk",
        files=_csv_file("customer_id,language\nc1,en\n"),
    )

    assert response.status_code == 422
    assert "raw_text" in response.json()["error"]["message"]
    assert "source" in response.json()["error"]["message"]


def test_bulk_csv_malformed_encoding_is_rejected(client) -> None:
    bad_bytes = b"source,raw_text\n\xff\xfe,broken\n"
    response = client.post(
        "/api/v1/feedback/bulk",
        files={"file": ("feedback.csv", io.BytesIO(bad_bytes), "text/csv")},
    )

    assert response.status_code == 422

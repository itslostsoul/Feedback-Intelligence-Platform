import uuid

from app.db.models.enums import ProcessingStatus, SourceType


def _payload(**overrides) -> dict:
    payload = {
        "source": "app_store",
        "source_type": "review",
        "raw_text": "The app crashes when I upload a photo.",
    }
    payload.update(overrides)
    return payload


def test_ingest_feedback_valid(client) -> None:
    response = client.post("/api/v1/feedback", json=_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["source"] == "app_store"
    assert body["raw_text"] == "The app crashes when I upload a photo."
    assert body["processing_status"] == ProcessingStatus.RECEIVED.value
    assert body["source_type"] == SourceType.REVIEW.value
    uuid.UUID(body["id"])  # well-formed UUID


def test_ingest_feedback_normalizes_whitespace(client) -> None:
    response = client.post(
        "/api/v1/feedback",
        json=_payload(raw_text="  needs trimming  ", customer_id="  cust-1  "),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["raw_text"] == "needs trimming"
    assert body["customer_id"] == "cust-1"


def test_ingest_feedback_missing_required_field_fails_validation(client) -> None:
    response = client.post("/api/v1/feedback", json={"source": "app_store"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_ingest_feedback_blank_raw_text_fails_validation(client) -> None:
    response = client.post("/api/v1/feedback", json=_payload(raw_text="   "))

    assert response.status_code == 422


def test_ingest_feedback_does_not_invent_optional_fields(client) -> None:
    response = client.post("/api/v1/feedback", json=_payload())

    body = response.json()
    assert body["external_id"] is None
    assert body["customer_id"] is None
    assert body["customer_tier"] is None
    assert body["language"] is None
    assert body["metadata"] is None


def test_get_feedback_by_id(client) -> None:
    created = client.post("/api/v1/feedback", json=_payload()).json()

    response = client.get(f"/api/v1/feedback/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_feedback_not_found(client) -> None:
    response = client.get(f"/api/v1/feedback/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_ingest_is_idempotent_on_source_and_external_id(client) -> None:
    payload = _payload(external_id="ext-123")

    first = client.post("/api/v1/feedback", json=payload)
    second = client.post("/api/v1/feedback", json=payload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    listed = client.get("/api/v1/feedback", params={"source": "app_store"}).json()
    assert listed["total"] == 1


def test_ingest_without_external_id_is_not_deduplicated(client) -> None:
    payload = _payload()

    first = client.post("/api/v1/feedback", json=payload)
    second = client.post("/api/v1/feedback", json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


def test_list_feedback_pagination(client) -> None:
    for i in range(5):
        client.post("/api/v1/feedback", json=_payload(external_id=f"page-{i}"))

    page1 = client.get("/api/v1/feedback", params={"limit": 2, "offset": 0}).json()
    page2 = client.get("/api/v1/feedback", params={"limit": 2, "offset": 2}).json()

    assert page1["total"] == 5
    assert len(page1["items"]) == 2
    assert len(page2["items"]) == 2
    ids_page1 = {item["id"] for item in page1["items"]}
    ids_page2 = {item["id"] for item in page2["items"]}
    assert ids_page1.isdisjoint(ids_page2)


def test_list_feedback_filters_by_source_type_and_status(client) -> None:
    client.post("/api/v1/feedback", json=_payload(external_id="a", source_type="review"))
    client.post("/api/v1/feedback", json=_payload(external_id="b", source_type="email"))

    reviews = client.get("/api/v1/feedback", params={"source_type": "review"}).json()
    emails = client.get("/api/v1/feedback", params={"source_type": "email"}).json()
    received = client.get("/api/v1/feedback", params={"status": "received"}).json()

    assert all(item["source_type"] == "review" for item in reviews["items"])
    assert all(item["source_type"] == "email" for item in emails["items"])
    assert received["total"] >= 2


def test_list_feedback_filters_by_source(client) -> None:
    client.post("/api/v1/feedback", json=_payload(source="zendesk", external_id="z1"))
    client.post("/api/v1/feedback", json=_payload(source="app_store", external_id="a1"))

    result = client.get("/api/v1/feedback", params={"source": "zendesk"}).json()

    assert result["total"] == 1
    assert result["items"][0]["source"] == "zendesk"

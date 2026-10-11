"""One comprehensive test proving the full product workflow works end to
end, driven entirely through the public HTTP API (exactly as a real
frontend would call it) -- not through direct service-layer calls like the
per-phase test files use for precision/isolation.

Feedback -> analysis -> embeddings -> semantic relationships -> issue
discovery -> signals -> decision -> evaluation -> brief -> second
evaluation (with new evidence) -> stability -> challenge.

Uses the existing FakeLLMProvider/FakeEmbeddingProvider fixtures (already
wired by `client` in tests/conftest.py) -- no real Gemini call is made,
keeping this test in the default, Gemini-independent suite.

LIMITATION (documented, not worked around): this test does not exercise
`POST /feedback/{id}/embed` + `POST /feedback/{id}/semantic-analysis`'s
*real* pgvector similarity search producing meaningful DUPLICATE/RELATED
edges from fake, non-semantically-meaningful vectors -- that combination is
already covered precisely in tests/test_semantic_engine.py. Here, the
embedding step is exercised for real (proving the endpoint works end to
end), but the relationship edges that issue discovery clusters on are
seeded directly via the same deterministic pattern used by
scripts/seed_demo_data.py and tests/test_issue_discovery.py, to keep this
test's outcome exactly predictable.
"""

import uuid

from app.db.models.enums import ProcessingStatus, RelationshipType


def _ingest(client, **overrides) -> dict:
    payload = {"source": "app_store", "raw_text": "Upload fails when the file is over 5MB."}
    payload.update(overrides)
    response = client.post("/api/v1/feedback", json=payload)
    assert response.status_code in (200, 201)
    return response.json()


def test_full_product_workflow_end_to_end(client, db_session) -> None:
    # Uses "upload" as the theme/keyword throughout -- the FIXED
    # affected_feature FakeLLMProvider's default analysis result always
    # returns (see tests/fakes.py), regardless of raw_text. This is
    # deliberately distinct from scripts/seed_demo_data.py's
    # "checkout"-themed issues, so this test's decision matching can never
    # collide with demo data that may already be committed in the same
    # database (see the note on step 5 below for why that matters).
    # --- 1. Feedback ingestion -------------------------------------------
    feedback_items = [
        _ingest(client, external_id="e2e-1", raw_text="Upload fails every time for files over 5MB.", customer_id="cust-1", customer_tier="enterprise", source="app_store"),
        _ingest(client, external_id="e2e-2", raw_text="The app crashes when uploading a large photo.", source="zendesk"),
        _ingest(client, external_id="e2e-3", raw_text="Upload error 500 on large attachments.", customer_id="cust-2", customer_tier="free", source="app_store"),
    ]
    assert all(item["processing_status"] == ProcessingStatus.RECEIVED.value for item in feedback_items)

    list_response = client.get("/api/v1/feedback", params={"limit": 10})
    assert list_response.status_code == 200
    assert list_response.json()["total"] >= 3

    # --- 2. Feedback analysis (FakeLLMProvider, no real Gemini call) -----
    for item in feedback_items:
        analyze_response = client.post(f"/api/v1/feedback/{item['id']}/analyze")
        assert analyze_response.status_code == 200

    for item in feedback_items:
        analysis_response = client.get(f"/api/v1/feedback/{item['id']}/analysis")
        assert analysis_response.status_code == 200
        assert analysis_response.json()["feedback_id"] == item["id"]

    # --- 3. Embedding (FakeEmbeddingProvider, no real Gemini call) -------
    for item in feedback_items:
        embed_response = client.post(f"/api/v1/feedback/{item['id']}/embed")
        assert embed_response.status_code == 200
        assert "vector" not in embed_response.json()["embedding"]  # never the raw vector

    # --- 4. Semantic relationships (seeded directly for a predictable
    # cluster -- see module docstring) -------------------------------------
    from app.services.semantic_engine import upsert_relationship

    ids = [uuid.UUID(item["id"]) for item in feedback_items]
    upsert_relationship(db_session, ids[0], ids[1], RelationshipType.RELATED, similarity=0.9)
    upsert_relationship(db_session, ids[1], ids[2], RelationshipType.RELATED, similarity=0.9)
    db_session.commit()

    # --- 5. Product issue discovery ---------------------------------------
    # NOTE: /issues/discover and GET /issues operate globally (not scoped
    # to this test's own rows), and the transactional-rollback isolation in
    # tests/conftest.py only protects against a test's OWN writes -- it
    # does not hide rows already committed in the database before the test
    # ran (e.g. scripts/seed_demo_data.py's output, if present). So this
    # test finds "its" issue by feedback_id membership rather than assuming
    # it's the only issue or the first one in an unfiltered list.
    discover_response = client.post("/api/v1/issues/discover")
    assert discover_response.status_code == 200
    assert discover_response.json()["issues_created"] >= 1

    issues_response = client.get("/api/v1/issues", params={"limit": 100})
    assert issues_response.status_code == 200
    our_feedback_ids = {item["id"] for item in feedback_items}
    issue = None
    for candidate in issues_response.json()["items"]:
        candidate_detail = client.get(f"/api/v1/issues/{candidate['id']}").json()
        if our_feedback_ids <= set(candidate_detail["feedback_ids"]):
            issue = candidate
            issue_detail = candidate_detail
            break
    assert issue is not None, "the issue discovered from this test's own feedback was not found"
    assert our_feedback_ids <= set(issue_detail["feedback_ids"])

    # --- 6. Issue signal calculation ---------------------------------------
    signals_response = client.post(f"/api/v1/issues/{issue['id']}/signals/recalculate")
    assert signals_response.status_code == 200
    assert signals_response.json()["feedback_count"] == 3

    get_signals_response = client.get(f"/api/v1/issues/{issue['id']}/signals")
    assert get_signals_response.status_code == 200

    # --- 7. Decision creation ------------------------------------------------
    decision_response = client.post(
        "/api/v1/decisions",
        json={"query_text": "Should we prioritize fixing upload?", "keywords": ["upload"]},
    )
    assert decision_response.status_code == 201
    decision_id = decision_response.json()["id"]

    # --- 8. Decision evaluation -----------------------------------------------
    evaluate_response = client.post(f"/api/v1/decisions/{decision_id}/evaluate")
    assert evaluate_response.status_code == 200
    evaluation_body = evaluate_response.json()
    assert len(evaluation_body["issues"]) == 1
    assert evaluation_body["issues"][0]["issue_title"] is not None  # enriched display field

    latest_eval_response = client.get(f"/api/v1/decisions/{decision_id}/evaluations/latest")
    assert latest_eval_response.status_code == 200

    # --- 9. Grounded decision brief (FakeLLMProvider) -------------------------
    brief_response = client.post(f"/api/v1/decisions/{decision_id}/brief")
    assert brief_response.status_code == 200
    brief_body = brief_response.json()
    assert brief_body["status"] == "completed"
    assert len(brief_body["citations"]) > 0
    for claim in brief_body["supporting_narrative"]:
        for citation_id in claim["citation_ids"]:
            assert citation_id in brief_body["citations"]  # every citation resolves

    # --- 10. Decision re-evaluation (new evidence arrives) --------------------
    extra = _ingest(client, external_id="e2e-4", raw_text="Upload is still broken, tried again today.", source="twitter")
    client.post(f"/api/v1/feedback/{extra['id']}/analyze")
    upsert_relationship(db_session, ids[0], uuid.UUID(extra["id"]), RelationshipType.RELATED, similarity=0.9)
    db_session.commit()
    client.post("/api/v1/issues/discover")
    client.post(f"/api/v1/issues/{issue['id']}/signals/recalculate")

    second_evaluate_response = client.post(f"/api/v1/decisions/{decision_id}/evaluate")
    assert second_evaluate_response.status_code == 200

    evaluations_list = client.get(f"/api/v1/decisions/{decision_id}/evaluations").json()
    assert evaluations_list["total"] == 2

    # --- 11. Historical stability comparison -----------------------------------
    stability_response = client.get(f"/api/v1/decisions/{decision_id}/stability")
    assert stability_response.status_code == 200
    stability_body = stability_response.json()
    assert stability_body["status"] != "insufficient_history"
    assert stability_body["evaluations_available"] == 2

    # --- 12. Evidence change inspection (explicit pairwise compare) -----------
    eval_ids = [e["id"] for e in evaluations_list["items"]]
    compare_response = client.get(
        f"/api/v1/decisions/{decision_id}/evaluations/{eval_ids[1]}/compare/{eval_ids[0]}"
    )
    assert compare_response.status_code == 200

    # --- 13. Challenge My Decision data retrieval ------------------------------
    challenge_response = client.get(f"/api/v1/decisions/{decision_id}/challenge")
    assert challenge_response.status_code == 200
    challenge_body = challenge_response.json()
    # Never a recommendation/verdict/score field anywhere in the response.
    assert {"recommendation", "score", "verdict", "winner"}.isdisjoint(challenge_body.keys())
    # Every event surfaced in a category bucket must trace back to
    # all_events -- the categorized view never invents or hides data.
    for category in ("supporting_changes", "counter_changes", "gap_changes"):
        for event in challenge_body[category]:
            assert event in challenge_body["all_events"]

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.errors import LLMOutputValidationError, LLMProviderError, LLMUngroundedOutputError
from app.db.models.decision import Decision
from app.db.models.decision_brief import DecisionBrief
from app.db.models.enums import EvidenceSufficiency, FeedbackCategory, GenerationStatus, ProductIssueStatus
from app.db.models.product_issue import ProductIssue
from app.db.models.product_issue_feedback import ProductIssueFeedback
from app.schemas.decision_brief import DecisionBriefLLMOutput, NarrativeClaim
from app.services.decision_brief import (
    DECISION_BRIEF_SYNTHESIS_VERSION,
    build_synthesis_input,
    compute_evidence_sufficiency,
    generate_brief,
    get_brief,
    get_latest_completed_brief,
    list_briefs,
    resolve_citations,
)
from app.services.decision_evidence import evaluate_decision, get_evidence_for_evaluation
from app.services.issue_signals import calculate_issue_signals
from tests.fakes import FakeLLMProvider

REFERENCE_TIME = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


# --- Fixture helpers, mirroring tests/test_decision_evidence.py ---------------


def _create_feedback(client, **overrides) -> dict:
    payload = {"source": "app_store", "raw_text": "The app crashes on checkout."}
    payload.update(overrides)
    return client.post("/api/v1/feedback", json=payload).json()


def _create_issue(
    db_session,
    feedback_ids: list[str],
    *,
    title: str = "Checkout fails during payment",
    affected_feature: str | None = "checkout",
    category: FeedbackCategory | None = None,
    status: ProductIssueStatus = ProductIssueStatus.OPEN,
) -> ProductIssue:
    issue = ProductIssue(
        canonical_feedback_id=uuid.UUID(feedback_ids[0]),
        title=title,
        affected_feature=affected_feature,
        category=category,
        status=status,
        clustering_version="cluster-v1",
    )
    db_session.add(issue)
    db_session.flush()
    for fid in feedback_ids:
        db_session.add(ProductIssueFeedback(product_issue_id=issue.id, feedback_id=uuid.UUID(fid)))
    db_session.commit()
    db_session.refresh(issue)
    return issue


def _create_decision(db_session, keywords=None, category=None, query_text="Should we prioritize fixing checkout?") -> Decision:
    decision = Decision(query_text=query_text, keywords=keywords or ["checkout"], category=category)
    db_session.add(decision)
    db_session.commit()
    db_session.refresh(decision)
    return decision


def _insert_analysis(db_session, feedback_id: str, severity: int) -> None:
    from app.db.models.feedback_analysis import FeedbackAnalysis

    db_session.add(
        FeedbackAnalysis(
            feedback_id=uuid.UUID(feedback_id),
            category=FeedbackCategory.BUG,
            affected_feature="checkout",
            severity=severity,
            confidence=0.9,
            summary="Checkout is broken.",
            provider="gemini",
            model="gemini-3.8-flash",
        )
    )
    db_session.commit()


def _sufficient_evidence_setup(client, db_session):
    """One issue with strong evidence (high frequency, multi-source,
    multi-segment, recent, increasing trend) matched by a "checkout" decision
    -> real Phase 4A SUFFICIENT evidence."""
    ids = []
    sources = ["app_store", "app_store", "app_store", "zendesk", "zendesk"]
    recent = (REFERENCE_TIME - timedelta(days=1)).isoformat()
    for i in range(5):
        fb = _create_feedback(client, external_id=f"suff-{i}", source=sources[i], created_at=recent)
        _insert_analysis(db_session, fb["id"], severity=8)
        ids.append(fb["id"])
    issue = _create_issue(db_session, ids)
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    decision = _create_decision(db_session)
    evaluation = evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)
    return decision, evaluation, issue


def _insufficient_evidence_setup(client, db_session):
    """A matched issue with ZERO linked feedback (canonical_feedback_id
    only, no ProductIssueFeedback membership) -> real Phase 4A gaps only,
    no supporting/counter fact at all.

    This is the only way to reach genuine INSUFFICIENT evidence under
    Phase 4A's existing (unmodified) rule set: with >=1 linked feedback
    item, recency is always either "recent" (fires sustained_recency,
    supporting) or "stale" (fires stale_evidence, counter — ungated by
    volume) — so any non-empty issue always carries at least one
    supporting or counter fact. Zero members makes recency "unknown"
    (neither), leaving only gaps — matching the spec's INSUFFICIENT
    semantics ("issues matched, but the available evidence is insufficient
    to make a meaningful evidence-backed description")."""
    anchor = _create_feedback(client, external_id="insuff-anchor")
    issue = ProductIssue(
        canonical_feedback_id=uuid.UUID(anchor["id"]),
        title="Checkout fails during payment",
        affected_feature="checkout",
        clustering_version="cluster-v1",
    )
    db_session.add(issue)
    db_session.commit()
    db_session.refresh(issue)
    calculate_issue_signals(db_session, issue, reference_time=REFERENCE_TIME)
    decision = _create_decision(db_session)
    evaluation = evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)
    return decision, evaluation, issue


def _no_matches_setup(db_session):
    decision = _create_decision(db_session, keywords=["nonexistent_feature_xyz"])
    evaluation = evaluate_decision(db_session, decision, reference_time=REFERENCE_TIME)
    return decision, evaluation


# ============================ Input DTO / registry ============================


def test_build_synthesis_input_shape(client, db_session) -> None:
    decision, evaluation, issue = _sufficient_evidence_setup(client, db_session)

    synthesis_input = build_synthesis_input(db_session, decision, evaluation)

    assert synthesis_input.decision_id == decision.id
    assert synthesis_input.decision_evaluation_id == evaluation.id
    assert synthesis_input.query_text == decision.query_text
    assert len(synthesis_input.issues) == 1
    assert synthesis_input.issues[0].product_issue_id == issue.id
    assert synthesis_input.issues[0].title == issue.title
    assert len(synthesis_input.issues[0].supporting) > 0


def test_build_synthesis_input_is_byte_identical_across_calls(client, db_session) -> None:
    decision, evaluation, _ = _sufficient_evidence_setup(client, db_session)

    first = build_synthesis_input(db_session, decision, evaluation)
    second = build_synthesis_input(db_session, decision, evaluation)

    assert first.model_dump() == second.model_dump()


def test_citation_registry_covers_every_fact(client, db_session) -> None:
    decision, evaluation, _ = _sufficient_evidence_setup(client, db_session)
    synthesis_input = build_synthesis_input(db_session, decision, evaluation)

    registry = synthesis_input.citation_registry()
    issue = synthesis_input.issues[0]
    all_facts = [*issue.supporting, *issue.counter, *issue.gaps]

    assert len(all_facts) > 0
    for fact in all_facts:
        assert fact.citation_id in registry
        assert registry[fact.citation_id].code == fact.code


def test_citation_ids_are_unique_within_an_evaluation(client, db_session) -> None:
    decision, evaluation, _ = _sufficient_evidence_setup(client, db_session)
    synthesis_input = build_synthesis_input(db_session, decision, evaluation)

    all_facts = [
        *[f for issue in synthesis_input.issues for f in (*issue.supporting, *issue.counter, *issue.gaps)],
        *synthesis_input.decision_level_gaps,
    ]
    citation_ids = [f.citation_id for f in all_facts]

    assert len(citation_ids) == len(set(citation_ids))


def test_citation_id_format_for_issue_and_decision_level_facts(client, db_session) -> None:
    decision, evaluation, issue = _sufficient_evidence_setup(client, db_session)
    synthesis_input = build_synthesis_input(db_session, decision, evaluation)

    for fact in synthesis_input.issues[0].supporting:
        assert fact.citation_id == f"{issue.id}:{fact.code}"

    no_match_decision, no_match_evaluation = _no_matches_setup(db_session)
    no_match_input = build_synthesis_input(db_session, no_match_decision, no_match_evaluation)
    for fact in no_match_input.decision_level_gaps:
        assert fact.citation_id == f"decision:{fact.code}"


# ============================ Evidence sufficiency =============================


def test_evidence_sufficiency_no_matches(db_session) -> None:
    _, evaluation = _no_matches_setup(db_session)
    evidence_rows = get_evidence_for_evaluation(db_session, evaluation.id)
    assert compute_evidence_sufficiency(evidence_rows) == EvidenceSufficiency.NO_MATCHES


def test_evidence_sufficiency_insufficient(client, db_session) -> None:
    _, evaluation, _ = _insufficient_evidence_setup(client, db_session)
    evidence_rows = get_evidence_for_evaluation(db_session, evaluation.id)
    assert compute_evidence_sufficiency(evidence_rows) == EvidenceSufficiency.INSUFFICIENT


def test_evidence_sufficiency_sufficient(client, db_session) -> None:
    _, evaluation, _ = _sufficient_evidence_setup(client, db_session)
    evidence_rows = get_evidence_for_evaluation(db_session, evaluation.id)
    assert compute_evidence_sufficiency(evidence_rows) == EvidenceSufficiency.SUFFICIENT


def test_evidence_sufficiency_is_authoritative_over_llm_prose(client, db_session) -> None:
    """Even if the fake LLM's headline claims strong confidence, the
    persisted evidence_sufficiency must reflect the deterministic backend
    computation, not anything the model wrote."""
    decision, evaluation, _ = _insufficient_evidence_setup(client, db_session)
    synthesis_input = build_synthesis_input(db_session, decision, evaluation)
    real_citation_id = next(iter(synthesis_input.citation_registry()))

    misleading_output = DecisionBriefLLMOutput(
        headline="The evidence is overwhelmingly strong and certain.",
        supporting_narrative=[NarrativeClaim(text="Strong support.", citation_ids=[real_citation_id])],
        counter_narrative=[],
        gap_narrative=[],
    )
    fake_provider = FakeLLMProvider(brief_result=misleading_output)

    brief = generate_brief(db_session, decision, evaluation, fake_provider)

    assert brief.evidence_sufficiency == EvidenceSufficiency.INSUFFICIENT
    assert brief.headline == "The evidence is overwhelmingly strong and certain."  # stored as-is, but flag is independent


# ============================ Valid grounded synthesis ==========================


def test_valid_grounded_synthesis_persists_completed_brief(client, db_session) -> None:
    decision, evaluation, issue = _sufficient_evidence_setup(client, db_session)
    fake_provider = FakeLLMProvider()

    brief = generate_brief(db_session, decision, evaluation, fake_provider)

    assert brief.status == GenerationStatus.COMPLETED
    assert brief.evidence_sufficiency == EvidenceSufficiency.SUFFICIENT
    assert brief.headline is not None
    assert brief.supporting_narrative is not None
    assert brief.error_code is None
    assert brief.provider is not None
    assert brief.model is not None
    assert brief.generation_version == DECISION_BRIEF_SYNTHESIS_VERSION


def test_citation_resolution_matches_persisted_evidence(client, db_session) -> None:
    decision, evaluation, issue = _sufficient_evidence_setup(client, db_session)
    citations = resolve_citations(db_session, evaluation)

    evidence_rows = get_evidence_for_evaluation(db_session, evaluation.id)
    row = evidence_rows[0]
    fact = row.supporting[0]
    citation_id = f"{issue.id}:{fact['code']}"

    assert citation_id in citations
    resolved = citations[citation_id]
    assert resolved.product_issue_id == issue.id
    assert resolved.issue_title == issue.title
    assert resolved.value == fact["value"]
    assert resolved.threshold == fact["threshold"]
    assert resolved.source_signal_path == fact["source_signal_path"]
    assert resolved.category == "supporting"


# ============================ Malformed / invalid output ========================


def test_malformed_output_raises_and_persists_failed(client, db_session) -> None:
    decision, evaluation, _ = _sufficient_evidence_setup(client, db_session)
    fake_provider = FakeLLMProvider(brief_raise_output_error=True)

    with pytest.raises(LLMOutputValidationError):
        generate_brief(db_session, decision, evaluation, fake_provider)

    briefs = db_session.execute(
        select(DecisionBrief).where(DecisionBrief.decision_evaluation_id == evaluation.id)
    ).scalars().all()
    assert len(briefs) == 1
    assert briefs[0].status == GenerationStatus.FAILED
    assert briefs[0].error_code == LLMOutputValidationError.error_code
    assert briefs[0].headline is None
    assert briefs[0].supporting_narrative is None


def test_provider_failure_raises_and_persists_failed(client, db_session) -> None:
    decision, evaluation, _ = _sufficient_evidence_setup(client, db_session)
    fake_provider = FakeLLMProvider(brief_raise_provider_error=True)

    with pytest.raises(LLMProviderError):
        generate_brief(db_session, decision, evaluation, fake_provider)

    briefs = db_session.execute(
        select(DecisionBrief).where(DecisionBrief.decision_evaluation_id == evaluation.id)
    ).scalars().all()
    assert len(briefs) == 1
    assert briefs[0].status == GenerationStatus.FAILED
    assert briefs[0].error_code == LLMProviderError.error_code
    assert briefs[0].headline is None


def test_unknown_citation_id_raises_ungrounded_error_and_persists_failed(client, db_session) -> None:
    decision, evaluation, _ = _sufficient_evidence_setup(client, db_session)
    fake_provider = FakeLLMProvider(brief_unknown_citation_id="nonexistent:fake_code")

    with pytest.raises(LLMUngroundedOutputError):
        generate_brief(db_session, decision, evaluation, fake_provider)

    briefs = db_session.execute(
        select(DecisionBrief).where(DecisionBrief.decision_evaluation_id == evaluation.id)
    ).scalars().all()
    assert len(briefs) == 1
    assert briefs[0].status == GenerationStatus.FAILED
    assert briefs[0].error_code == "llm_ungrounded_output"
    assert briefs[0].headline is None
    assert briefs[0].supporting_narrative is None
    assert briefs[0].counter_narrative is None
    assert briefs[0].gap_narrative is None


def test_no_untrusted_narrative_persisted_on_any_failure_path(client, db_session) -> None:
    decision, evaluation, _ = _sufficient_evidence_setup(client, db_session)

    for provider in (
        FakeLLMProvider(brief_raise_provider_error=True),
        FakeLLMProvider(brief_raise_output_error=True),
        FakeLLMProvider(brief_unknown_citation_id="bad:citation"),
    ):
        with pytest.raises((LLMProviderError, LLMOutputValidationError, LLMUngroundedOutputError)):
            generate_brief(db_session, decision, evaluation, provider)

    briefs = db_session.execute(
        select(DecisionBrief).where(DecisionBrief.decision_evaluation_id == evaluation.id)
    ).scalars().all()
    assert len(briefs) == 3
    for brief in briefs:
        assert brief.status == GenerationStatus.FAILED
        assert brief.headline is None
        assert brief.supporting_narrative is None
        assert brief.counter_narrative is None
        assert brief.gap_narrative is None


# ============================ Pydantic schema-level validation ==================


def test_narrative_claim_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        NarrativeClaim.model_validate({"text": "x", "citation_ids": ["a:b"], "confidence": 0.9})


def test_decision_brief_output_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        DecisionBriefLLMOutput.model_validate(
            {
                "headline": "x",
                "supporting_narrative": [],
                "counter_narrative": [],
                "gap_narrative": [],
                "priority_score": 0.9,
            }
        )


def test_narrative_claim_rejects_empty_citation_ids() -> None:
    with pytest.raises(ValidationError):
        NarrativeClaim.model_validate({"text": "x", "citation_ids": []})


def test_narrative_claim_rejects_blank_text() -> None:
    with pytest.raises(ValidationError):
        NarrativeClaim.model_validate({"text": "   ", "citation_ids": ["a:b"]})


def test_narrative_claim_rejects_text_over_max_length() -> None:
    with pytest.raises(ValidationError):
        NarrativeClaim.model_validate({"text": "x" * 281, "citation_ids": ["a:b"]})


# ============================ Regeneration / metadata ===========================


def test_regeneration_creates_multiple_decision_brief_rows(client, db_session) -> None:
    decision, evaluation, _ = _sufficient_evidence_setup(client, db_session)
    fake_provider = FakeLLMProvider()

    first = generate_brief(db_session, decision, evaluation, fake_provider)
    second = generate_brief(db_session, decision, evaluation, fake_provider)

    assert first.id != second.id
    briefs = db_session.execute(
        select(DecisionBrief).where(DecisionBrief.decision_evaluation_id == evaluation.id)
    ).scalars().all()
    assert len(briefs) == 2


def test_provider_model_version_metadata_stamped_on_success_and_failure(client, db_session) -> None:
    decision, evaluation, _ = _sufficient_evidence_setup(client, db_session)

    completed = generate_brief(db_session, decision, evaluation, FakeLLMProvider())
    assert completed.provider
    assert completed.model
    assert completed.generation_version == DECISION_BRIEF_SYNTHESIS_VERSION

    with pytest.raises(LLMProviderError):
        generate_brief(db_session, decision, evaluation, FakeLLMProvider(brief_raise_provider_error=True))
    failed = db_session.execute(
        select(DecisionBrief).where(DecisionBrief.status == GenerationStatus.FAILED)
    ).scalar_one()
    assert failed.provider
    assert failed.model
    assert failed.generation_version == DECISION_BRIEF_SYNTHESIS_VERSION


# ============================ Full Phase 4A round-trip ===========================


def test_full_phase_4a_roundtrip_no_drift(client, db_session) -> None:
    decision, evaluation, issue = _sufficient_evidence_setup(client, db_session)
    synthesis_input = build_synthesis_input(db_session, decision, evaluation)
    real_citation_id = next(iter(synthesis_input.citation_registry()))

    output = DecisionBriefLLMOutput(
        headline="Checkout evidence summary.",
        supporting_narrative=[NarrativeClaim(text="Grounded claim.", citation_ids=[real_citation_id])],
        counter_narrative=[],
        gap_narrative=[],
    )
    fake_provider = FakeLLMProvider(brief_result=output)

    brief = generate_brief(db_session, decision, evaluation, fake_provider)
    citations = resolve_citations(db_session, evaluation)

    evidence_rows = get_evidence_for_evaluation(db_session, evaluation.id)
    persisted_fact = next(
        f for row in evidence_rows for f in row.supporting if f"{row.product_issue_id}:{f['code']}" == real_citation_id
    )

    resolved = citations[real_citation_id]
    assert resolved.value == persisted_fact["value"]
    assert resolved.threshold == persisted_fact["threshold"]
    assert resolved.source_signal_path == persisted_fact["source_signal_path"]
    assert brief.supporting_narrative[0]["citation_ids"] == [real_citation_id]


# ================================= API ============================


def test_generate_brief_endpoint_end_to_end(client, db_session) -> None:
    decision, evaluation, issue = _sufficient_evidence_setup(client, db_session)

    response = client.post(f"/api/v1/decisions/{decision.id}/brief")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["evidence_sufficiency"] == "sufficient"
    assert body["headline"]
    assert len(body["citations"]) > 0
    for claim in body["supporting_narrative"]:
        for citation_id in claim["citation_ids"]:
            assert citation_id in body["citations"]


def test_generate_brief_endpoint_404_missing_decision(client) -> None:
    response = client.post(f"/api/v1/decisions/{uuid.uuid4()}/brief")
    assert response.status_code == 404


def test_generate_brief_endpoint_404_no_evaluation(client, db_session) -> None:
    decision = _create_decision(db_session)
    response = client.post(f"/api/v1/decisions/{decision.id}/brief")
    assert response.status_code == 404


def test_get_latest_brief_endpoint(client, db_session) -> None:
    decision, evaluation, _ = _sufficient_evidence_setup(client, db_session)

    not_found = client.get(f"/api/v1/decisions/{decision.id}/brief")
    assert not_found.status_code == 404

    client.post(f"/api/v1/decisions/{decision.id}/brief")
    second = client.post(f"/api/v1/decisions/{decision.id}/brief").json()

    latest = client.get(f"/api/v1/decisions/{decision.id}/brief")
    assert latest.status_code == 200
    assert latest.json()["id"] == second["id"]


def test_brief_history_endpoint_includes_completed_and_failed(client, db_session) -> None:
    from app.api import dependencies

    decision, evaluation, _ = _sufficient_evidence_setup(client, db_session)

    client.post(f"/api/v1/decisions/{decision.id}/brief")  # completed via default fixture fake

    app_fake = FakeLLMProvider(brief_raise_provider_error=True)
    from app.main import app as fastapi_app

    fastapi_app.dependency_overrides[dependencies.get_llm_provider] = lambda: app_fake
    try:
        client.post(f"/api/v1/decisions/{decision.id}/brief")
    finally:
        fastapi_app.dependency_overrides[dependencies.get_llm_provider] = lambda: FakeLLMProvider()

    history = client.get(f"/api/v1/decisions/{decision.id}/brief/history")
    assert history.status_code == 200
    body = history.json()
    assert body["total"] == 2
    statuses = {item["status"] for item in body["items"]}
    assert statuses == {"completed", "failed"}


def test_brief_history_pagination(client, db_session) -> None:
    decision, evaluation, _ = _sufficient_evidence_setup(client, db_session)
    for _ in range(3):
        client.post(f"/api/v1/decisions/{decision.id}/brief")

    page = client.get(f"/api/v1/decisions/{decision.id}/brief/history", params={"limit": 2, "offset": 0})
    assert page.status_code == 200
    body = page.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2


def test_get_specific_brief_endpoint(client, db_session) -> None:
    decision, evaluation, _ = _sufficient_evidence_setup(client, db_session)
    created = client.post(f"/api/v1/decisions/{decision.id}/brief").json()

    response = client.get(f"/api/v1/decisions/{decision.id}/brief/{created['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]
    assert "citations" in response.json()


def test_get_specific_brief_404_for_unknown_id(client, db_session) -> None:
    decision, evaluation, _ = _sufficient_evidence_setup(client, db_session)
    response = client.get(f"/api/v1/decisions/{decision.id}/brief/{uuid.uuid4()}")
    assert response.status_code == 404


def test_get_specific_brief_404_for_mismatched_decision(client, db_session) -> None:
    decision_a, evaluation_a, _ = _sufficient_evidence_setup(client, db_session)
    brief = client.post(f"/api/v1/decisions/{decision_a.id}/brief").json()

    decision_b = _create_decision(db_session, keywords=["something_else"], query_text="Unrelated question?")

    response = client.get(f"/api/v1/decisions/{decision_b.id}/brief/{brief['id']}")
    assert response.status_code == 404


def test_no_matches_end_to_end_via_api(db_session, client) -> None:
    decision = _create_decision(db_session, keywords=["nonexistent_feature_xyz"])
    client.post(f"/api/v1/decisions/{decision.id}/evaluate")

    response = client.post(f"/api/v1/decisions/{decision.id}/brief")

    assert response.status_code == 200
    body = response.json()
    assert body["evidence_sufficiency"] == "no_matches"

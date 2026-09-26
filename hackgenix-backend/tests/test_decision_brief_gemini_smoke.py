"""Opt-in smoke test for Phase 4B against the REAL Gemini API.

Not part of the default test invariant ("no test calls Gemini") — every
other test in this suite uses FakeLLMProvider. This file exists to catch
drift between our assumptions about the Gemini structured-output API and
its actual behavior (exactly the kind of thing that once required the
hand-built genai_types.Schema workaround for `additionalProperties` in
GeminiProvider.analyze_feedback — only a real call can catch this class of
issue).

Excluded from the default `pytest` invocation by `pytest.ini`'s
`addopts = -m "not gemini_smoke"` — this is the primary guarantee (it holds
even though GEMINI_API_KEY happens to be configured in this environment's
.env, which a bare `skipif` on the key alone would NOT have protected
against). Run explicitly with:

    pytest -m gemini_smoke tests/test_decision_brief_gemini_smoke.py

The `skipif` below is a secondary guard for anyone who opts in without a
configured key.
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.core.config import get_settings
from app.db.models.decision import Decision
from app.db.models.enums import GenerationStatus, ProductIssueStatus
from app.db.models.product_issue import ProductIssue
from app.db.models.product_issue_feedback import ProductIssueFeedback
from app.services.decision_brief import generate_brief
from app.services.decision_evidence import evaluate_decision
from app.services.issue_signals import calculate_issue_signals
from app.services.llm.factory import build_llm_provider

pytestmark = [
    pytest.mark.gemini_smoke,
    pytest.mark.skipif(
        not get_settings().GEMINI_API_KEY,
        reason="GEMINI_API_KEY not configured — skipping real-Gemini smoke test.",
    ),
]


def test_real_gemini_produces_valid_grounded_brief(client, db_session) -> None:
    fb = client.post(
        "/api/v1/feedback",
        json={"source": "app_store", "raw_text": "Checkout crashes every time I try to pay.", "external_id": "smoke-1"},
    ).json()
    issue = ProductIssue(
        canonical_feedback_id=uuid.UUID(fb["id"]),
        title="Checkout fails during payment",
        affected_feature="checkout",
        status=ProductIssueStatus.OPEN,
        clustering_version="cluster-v1",
    )
    db_session.add(issue)
    db_session.flush()
    db_session.add(ProductIssueFeedback(product_issue_id=issue.id, feedback_id=uuid.UUID(fb["id"])))
    db_session.commit()
    db_session.refresh(issue)

    reference_time = datetime.now(timezone.utc)
    calculate_issue_signals(db_session, issue, reference_time=reference_time)

    decision = Decision(query_text="Should we prioritize fixing checkout?", keywords=["checkout"])
    db_session.add(decision)
    db_session.commit()
    db_session.refresh(decision)

    evaluation = evaluate_decision(db_session, decision, reference_time=reference_time)

    provider = build_llm_provider()
    brief = generate_brief(db_session, decision, evaluation, provider)

    assert brief.status == GenerationStatus.COMPLETED
    assert brief.headline
    assert isinstance(brief.supporting_narrative, list)

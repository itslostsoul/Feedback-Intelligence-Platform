"""Deterministic, repeatable demo-data seed for the Hackgenix backend.

Populates a full, realistic story across every implemented phase so a
frontend developer (or a live demo) can exercise the entire product without
waiting on real user data:

    Feedback (multiple customers/tiers/sources/severities)
      -> analysis (deterministic, scripted -- no Gemini call required)
      -> semantic relationships (explicit, not similarity-search-derived)
      -> product issue discovery (real app.services.issue_discovery)
      -> signal calculation (real app.services.issue_signals)
      -> a Decision ("Should we prioritize fixing checkout?")
      -> a FIRST evaluation (partial evidence, with real gaps)
      -> more feedback arrives, evidence grows and shifts
      -> a SECOND evaluation (different evidence: a resolved gap, a newly
         appeared counter-fact, a trend reversal, a disappeared issue, a
         newly-appeared issue)
      -> (optional, only if GEMINI_API_KEY is configured) a grounded
         Decision Brief for the second evaluation

This produces a genuinely MIXED, nuanced stability/Challenge-My-Decision
result -- not a trivially one-directional "everything got better" demo --
because that is what makes Phase 4C/Challenge worth demonstrating.

DESIGN NOTES (why some steps don't call the real LLM/embedding providers):
  - Feedback ANALYSIS uses a small local `_ScriptedLLMProvider` passed into
    the REAL `app.services.feedback_analysis.analyze_feedback` service (not
    hand-inserted FeedbackAnalysis rows) -- so the same status-transition
    business rules (RECEIVED -> ANALYZING -> ANALYZED/NEEDS_REVIEW) run for
    real. This keeps the seed deterministic and Gemini-independent while
    still exercising the real service function, per "prefer existing
    service functions over bypassing business rules."
  - Semantic RELATIONSHIPS are created directly via the real
    `app.services.semantic_engine.upsert_relationship` (not via embeddings +
    similarity search) so the demo's clustering is exactly controlled and
    reproducible -- engineering fake embeddings to reliably produce a
    specific similarity score would be fragile and add no real value here.
  - Issue DISCOVERY and SIGNAL CALCULATION and DECISION EVALUATION are the
    real, fully deterministic Phase 3B-3/3C/4A services, unmodified.
  - The Decision BRIEF (Phase 4B) is the only step that genuinely needs an
    LLM. It is attempted ONLY if GEMINI_API_KEY/LLM_PROVIDER/LLM_MODEL are
    configured, wrapped in try/except -- the rest of the seed (and the
    whole demo) works correctly without it.

SAFETY:
  - Never imported or run automatically by the application. Only runs when
    invoked directly: `python scripts/seed_demo_data.py`.
  - Refuses to run against a database that already has a `decision` row,
    unless `--force` is passed, so it can never silently overwrite/duplicate
    real or previous demo data.
  - Uses the application's own configured DATABASE_URL (via Settings) --
    point `.env` at a development/demo database, never production.

Usage:
    python scripts/seed_demo_data.py            # seed (refuses if data exists)
    python scripts/seed_demo_data.py --force     # wipe demo-relevant tables first
"""

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.errors import LLMConfigurationError, LLMOutputValidationError, LLMProviderError  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402
from app.db.models.decision import Decision  # noqa: E402
from app.db.models.enums import FeedbackCategory, ProductIssueStatus, RelationshipType, SourceType  # noqa: E402
from app.db.models.feedback import Feedback  # noqa: E402
from app.db.models.product_issue import ProductIssue  # noqa: E402
from app.schemas.analysis import FeedbackAnalysisResult  # noqa: E402
from app.schemas.feedback import FeedbackCreate  # noqa: E402
from app.services.decision_brief import generate_brief  # noqa: E402
from app.services.decision_evidence import evaluate_decision  # noqa: E402
from app.services.feedback_analysis import analyze_feedback  # noqa: E402
from app.services.feedback_ingestion import create_or_get_feedback  # noqa: E402
from app.services.issue_discovery import run_issue_discovery  # noqa: E402
from app.services.issue_signals import calculate_issue_signals  # noqa: E402
from app.services.llm.factory import build_llm_provider  # noqa: E402
from app.services.semantic_engine import upsert_relationship  # noqa: E402


class _ScriptedLLMProvider:
    """A deterministic LLMProvider used only by this seed script, so
    `analyze_feedback` (the real service) can run without any network call.
    Returns a pre-written FeedbackAnalysisResult per feedback item, keyed by
    the feedback's external_id."""

    def __init__(self, results_by_external_id: dict[str, FeedbackAnalysisResult]) -> None:
        self._results = results_by_external_id

    def analyze_feedback(self, raw_text: str) -> FeedbackAnalysisResult:
        # Matched up by the caller passing the right feedback in; this
        # provider is reconstructed per-call with a single-item dict (see
        # _analyze) so raw_text isn't actually needed to select the result.
        return next(iter(self._results.values()))


# --- Demo narrative timeline --------------------------------------------------
NOW = datetime.now(timezone.utc)
T_FIRST_EVAL = NOW - timedelta(days=8)   # E1: reference_time of the first evaluation
T_SECOND_EVAL = NOW                       # E2: reference_time of the second evaluation


def _ingest(db, *, external_id, source, source_type, raw_text, customer_id=None, customer_tier=None, created_at=None):
    payload = FeedbackCreate(
        external_id=external_id,
        source=source,
        source_type=source_type,
        raw_text=raw_text,
        customer_id=customer_id,
        customer_tier=customer_tier,
        created_at=created_at,
    )
    feedback, _ = create_or_get_feedback(db, payload)
    return feedback


def _analyze(db, feedback: Feedback, *, category: FeedbackCategory, affected_feature: str, severity: int, summary: str) -> None:
    result = FeedbackAnalysisResult(
        category=category, affected_feature=affected_feature, severity=severity, confidence=0.9, summary=summary
    )
    provider = _ScriptedLLMProvider({str(feedback.id): result})
    analyze_feedback(db, feedback, provider)


def _relate_chain(db, feedback_ids: list[uuid.UUID], relationship_type: RelationshipType) -> None:
    """Connects every item in `feedback_ids` into one component via a chain
    of relationships, so app.services.issue_discovery's connected-components
    clustering groups them into a single ProductIssue."""
    for a, b in zip(feedback_ids, feedback_ids[1:]):
        upsert_relationship(db, a, b, relationship_type, similarity=0.93)
    db.commit()


def _find_issue_by_canonical(db, canonical_feedback_id: uuid.UUID) -> ProductIssue:
    return db.execute(select(ProductIssue).where(ProductIssue.canonical_feedback_id == canonical_feedback_id)).scalar_one()


def _already_seeded(db) -> bool:
    return db.execute(select(Decision.id).limit(1)).scalar_one_or_none() is not None


def seed(db, *, force: bool) -> None:
    if _already_seeded(db) and not force:
        print("Demo data already present (a Decision row exists). Re-run with --force to add another demo run.")
        print("This script never deletes existing rows even with --force -- it only proceeds instead of refusing.")
        raise SystemExit(1)

    print(f"Seeding demo data. E1 reference_time={T_FIRST_EVAL.isoformat()}  E2 reference_time={T_SECOND_EVAL.isoformat()}")

    # ======================================================================
    # Issue 1 (the main demo issue): "Checkout" -- Phase A, present at E1
    # ======================================================================
    checkout_recent = [
        ("checkout-a1", T_FIRST_EVAL - timedelta(days=1), "app_store", SourceType.REVIEW, "cust-acme", "enterprise"),
        ("checkout-a2", T_FIRST_EVAL - timedelta(days=2), "app_store", SourceType.REVIEW, None, None),
        ("checkout-a3", T_FIRST_EVAL - timedelta(days=3), "zendesk", SourceType.SUPPORT_TICKET, "cust-hooli", "free"),
    ]
    checkout_older = [
        ("checkout-a4", T_FIRST_EVAL - timedelta(days=10), "app_store", SourceType.REVIEW, None, None),
    ]
    checkout_texts = {
        "checkout-a1": "Checkout fails every time I try to pay with my saved card.",
        "checkout-a2": "The app crashes right when I hit 'Place Order' at checkout.",
        "checkout-a3": "Customer reports checkout error code 500 on the payment step.",
        "checkout-a4": "Checkout button does nothing when tapped on the review screen.",
    }
    # Analyzed BEFORE discovery: _summarize_cluster needs at least some
    # analyzed members to assign a real title/affected_feature at creation
    # time -- with zero analyzed members it falls back to a generic
    # "Cluster of N..." title and affected_feature=None, which would break
    # this decision's keyword matching entirely. (feedback_count is still
    # only 4 at E1 -- below MIN_VOLUME_FOR_JUDGEMENT=5 -- so
    # low_volume_overall / insufficient_source_diversity_data gaps still
    # fire at E1 regardless of analysis, giving a real "gap resolves at E2"
    # story once volume grows to 8.)
    original_severities = {"checkout-a1": 6, "checkout-a2": 7, "checkout-a3": 8, "checkout-a4": 4}
    checkout_ids: list[uuid.UUID] = []
    for ext_id, created_at, source, source_type, cust_id, cust_tier in checkout_recent + checkout_older:
        fb = _ingest(
            db, external_id=ext_id, source=source, source_type=source_type, raw_text=checkout_texts[ext_id],
            customer_id=cust_id, customer_tier=cust_tier, created_at=created_at,
        )
        _analyze(db, fb, category=FeedbackCategory.BUG, affected_feature="checkout", severity=original_severities[ext_id], summary=checkout_texts[ext_id])
        checkout_ids.append(fb.id)
    _relate_chain(db, checkout_ids, RelationshipType.RELATED)
    run_issue_discovery(db)
    checkout_issue = _find_issue_by_canonical(db, min(checkout_ids))
    calculate_issue_signals(db, checkout_issue, reference_time=T_FIRST_EVAL)
    print(f"Issue 1 'Checkout' created: {checkout_issue.id} (4 members; low volume -> gaps fire at E1)")

    # ======================================================================
    # Issue 2: "Checkout session timeout" -- present at E1, RESOLVED before E2
    # ======================================================================
    timeout_items = [
        ("timeout-1", T_FIRST_EVAL - timedelta(days=1), "app_store", SourceType.REVIEW, "cust-globex", "enterprise", 8),
        ("timeout-2", T_FIRST_EVAL - timedelta(days=2), "zendesk", SourceType.SUPPORT_TICKET, "cust-initech", "pro", 7),
        ("timeout-3", T_FIRST_EVAL - timedelta(days=2), "twitter", SourceType.SOCIAL, None, None, 8),
        ("timeout-4", T_FIRST_EVAL - timedelta(days=3), "app_store", SourceType.REVIEW, None, None, 7),
        ("timeout-5", T_FIRST_EVAL - timedelta(days=1), "zendesk", SourceType.SUPPORT_TICKET, "cust-globex", "enterprise", 8),
    ]
    timeout_texts = {
        "timeout-1": "My checkout session times out before I can finish paying.",
        "timeout-2": "Session expires mid-checkout and I lose my cart.",
        "timeout-3": "Checkout times out constantly, so frustrating.",
        "timeout-4": "Checkout session dies after 30 seconds of typing my address.",
        "timeout-5": "Timeout error during checkout on slow wifi.",
    }
    timeout_ids = []
    for ext_id, created_at, source, source_type, cust_id, cust_tier, severity in timeout_items:
        fb = _ingest(
            db, external_id=ext_id, source=source, source_type=source_type, raw_text=timeout_texts[ext_id],
            customer_id=cust_id, customer_tier=cust_tier, created_at=created_at,
        )
        _analyze(db, fb, category=FeedbackCategory.PERFORMANCE, affected_feature="checkout-timeout", severity=severity, summary=timeout_texts[ext_id])
        timeout_ids.append(fb.id)
    _relate_chain(db, timeout_ids, RelationshipType.RELATED)
    run_issue_discovery(db)
    timeout_issue = _find_issue_by_canonical(db, min(timeout_ids))
    calculate_issue_signals(db, timeout_issue, reference_time=T_FIRST_EVAL)
    print(f"Issue 2 'Checkout timeout' created: {timeout_issue.id} (5 members, strong evidence -- will disappear at E2)")

    # ======================================================================
    # Decision + FIRST evaluation (E1)
    # ======================================================================
    decision = Decision(query_text="Should we prioritize fixing checkout?", keywords=["checkout"])
    db.add(decision)
    db.commit()
    db.refresh(decision)

    evaluation_1 = evaluate_decision(db, decision, reference_time=T_FIRST_EVAL)
    print(f"Decision created: {decision.id}")
    print(f"Evaluation 1 (E1): {evaluation_1.id} -- matches issue 1 (partial evidence) + issue 2 (strong evidence)")

    # ======================================================================
    # Growth phase: new evidence arrives before E2
    # ======================================================================
    # New "Checkout" feedback: b1/b2 dated inside E2's *current* trend
    # window (so only 2 land there); b3/b4 dated well outside BOTH of E2's
    # trend windows (>14 days back) so they add pure volume/segment/source
    # diversity without disturbing the trend count. Combined with the 3
    # original items that age into E2's *previous* window (see the
    # T_FIRST_EVAL comment above), current(2) < previous(3) at E2 ->
    # increasing_trend (E1) reverses to declining_trend (E2), a real
    # COUNTER-evidence fact newly appearing. All connected into the SAME
    # component as the original 4 (via a relation to checkout-a1), so
    # run_issue_discovery grows the existing issue additively rather than
    # creating a new one.
    checkout_growth = [
        ("checkout-b1", T_SECOND_EVAL - timedelta(days=1), "zendesk", SourceType.SUPPORT_TICKET, "cust-globex", "enterprise"),
        ("checkout-b2", T_SECOND_EVAL - timedelta(days=2), "twitter", SourceType.SOCIAL, None, None),
        ("checkout-b3", T_SECOND_EVAL - timedelta(days=20), "app_store", SourceType.REVIEW, "cust-initech", "pro"),
        ("checkout-b4", T_SECOND_EVAL - timedelta(days=25), "app_store", SourceType.REVIEW, None, None),
    ]
    checkout_growth_texts = {
        "checkout-b1": "Checkout is still broken -- payment declined with no reason given.",
        "checkout-b2": "Checkout keeps failing, second time this week.",
        "checkout-b3": "Getting a generic error at checkout, can't complete my order.",
        "checkout-b4": "Checkout page froze and charged my card twice.",
    }
    growth_ids = []
    for ext_id, created_at, source, source_type, cust_id, cust_tier in checkout_growth:
        fb = _ingest(
            db, external_id=ext_id, source=source, source_type=source_type, raw_text=checkout_growth_texts[ext_id],
            customer_id=cust_id, customer_tier=cust_tier, created_at=created_at,
        )
        _analyze(db, fb, category=FeedbackCategory.BUG, affected_feature="checkout", severity=5, summary=checkout_growth_texts[ext_id])
        growth_ids.append(fb.id)

    # The new wave of complaints is connected via DUPLICATE (near-identical
    # "still broken" reports), not RELATED -- realistic, and it visibly
    # moves duplicate_ratio from 0.0 at E1 to a real, nonzero value at E2
    # (a VALUE_CHANGED fact on duplicate_ratio/duplicate_pair_count), even
    # though it doesn't cross Phase 4A's "high" concentration threshold
    # here (that would require a much higher duplicate:related edge ratio
    # than a realistic connectivity chain allows) -- an honest, undramatized
    # data point rather than one engineered purely to trip a threshold.
    upsert_relationship(db, checkout_ids[0], growth_ids[0], RelationshipType.DUPLICATE, similarity=0.95)
    for a, b in zip(growth_ids, growth_ids[1:]):
        upsert_relationship(db, a, b, RelationshipType.DUPLICATE, similarity=0.95)
    db.commit()

    run_issue_discovery(db)
    checkout_issue = _find_issue_by_canonical(db, min(checkout_ids))  # refetch after growth
    calculate_issue_signals(db, checkout_issue, reference_time=T_SECOND_EVAL)
    print(f"Issue 1 'Checkout' grown to 8 members (4 original + 4 new); trend timing engineered to reverse increasing -> decreasing")

    # Issue 2 "Checkout timeout" gets resolved -- disappears from E2.
    timeout_issue.status = ProductIssueStatus.RESOLVED
    db.add(timeout_issue)
    db.commit()
    print(f"Issue 2 'Checkout timeout' marked RESOLVED -- will disappear from the decision's matched issues at E2")

    # ======================================================================
    # Issue 3: "Checkout international card declined" -- did not exist at
    # E1, appears fresh before E2.
    # ======================================================================
    intl_items = [
        ("intl-1", T_SECOND_EVAL - timedelta(days=1), "app_store", SourceType.REVIEW, "cust-acme", "enterprise", 7),
        ("intl-2", T_SECOND_EVAL - timedelta(days=2), "zendesk", SourceType.SUPPORT_TICKET, None, None, 6),
        ("intl-3", T_SECOND_EVAL - timedelta(days=1), "twitter", SourceType.SOCIAL, "cust-hooli", "free", 8),
        ("intl-4", T_SECOND_EVAL - timedelta(days=2), "app_store", SourceType.REVIEW, None, None, 6),
        ("intl-5", T_SECOND_EVAL - timedelta(days=1), "zendesk", SourceType.SUPPORT_TICKET, "cust-initech", "pro", 7),
    ]
    intl_texts = {
        "intl-1": "International card declined at checkout for no clear reason.",
        "intl-2": "Can't check out with a non-US credit card, always fails.",
        "intl-3": "Checkout rejects my international Visa every time.",
        "intl-4": "Paying with a foreign card at checkout just doesn't work.",
        "intl-5": "International customers can't complete checkout at all.",
    }
    intl_ids = []
    for ext_id, created_at, source, source_type, cust_id, cust_tier, severity in intl_items:
        fb = _ingest(
            db, external_id=ext_id, source=source, source_type=source_type, raw_text=intl_texts[ext_id],
            customer_id=cust_id, customer_tier=cust_tier, created_at=created_at,
        )
        _analyze(db, fb, category=FeedbackCategory.BUG, affected_feature="checkout-international", severity=severity, summary=intl_texts[ext_id])
        intl_ids.append(fb.id)
    _relate_chain(db, intl_ids, RelationshipType.RELATED)
    run_issue_discovery(db)
    intl_issue = _find_issue_by_canonical(db, min(intl_ids))
    calculate_issue_signals(db, intl_issue, reference_time=T_SECOND_EVAL)
    print(f"Issue 3 'Checkout international' created: {intl_issue.id} (5 members) -- will appear as NEW at E2")

    # ======================================================================
    # SECOND evaluation (E2) + stability/challenge preview
    # ======================================================================
    evaluation_2 = evaluate_decision(db, decision, reference_time=T_SECOND_EVAL)
    print(f"Evaluation 2 (E2): {evaluation_2.id} -- matches issue 1 (grown) + issue 3 (new); issue 2 gone")

    # ======================================================================
    # Optional: grounded Decision Brief for E2 (only if Gemini is configured)
    # ======================================================================
    settings = get_settings()
    if settings.GEMINI_API_KEY and settings.LLM_PROVIDER and settings.LLM_MODEL:
        try:
            provider = build_llm_provider(settings)
            brief = generate_brief(db, decision, evaluation_2, provider, settings)
            print(f"Decision Brief generated for E2: {brief.id} (status={brief.status.value})")
        except (LLMConfigurationError, LLMProviderError, LLMOutputValidationError) as exc:
            print(f"Decision Brief generation skipped (LLM call failed: {exc.message}). Everything else seeded successfully.")
    else:
        print("Decision Brief generation skipped (GEMINI_API_KEY/LLM_PROVIDER/LLM_MODEL not configured). "
              "Call POST /api/v1/decisions/{id}/brief yourself once configured, or demo without it -- "
              "stability/challenge/evidence work regardless.")

    print()
    print("=" * 70)
    print("Demo seed complete. Suggested first calls for the frontend:")
    print(f"  GET  /api/v1/decisions/{decision.id}")
    print(f"  GET  /api/v1/decisions/{decision.id}/evaluations/latest")
    print(f"  GET  /api/v1/decisions/{decision.id}/stability")
    print(f"  GET  /api/v1/decisions/{decision.id}/challenge")
    print(f"  GET  /api/v1/issues/{checkout_issue.id}")
    print(f"  GET  /api/v1/issues/{checkout_issue.id}/signals")
    print("=" * 70)


def main() -> int:
    force = "--force" in sys.argv[1:]
    db = SessionLocal()
    try:
        seed(db, force=force)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

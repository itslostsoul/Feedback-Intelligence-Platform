import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.decision import Decision
from app.db.models.decision_evaluation import DecisionEvaluation
from app.db.models.decision_issue_evidence import DecisionIssueEvidence
from app.db.models.product_issue_signal_snapshot import ProductIssueSignalSnapshot
from app.services.decision_matching import find_matching_issues
from app.services.issue_signals import (
    DUPLICATE_CONCENTRATION_MODERATE_MAX,
    MIN_SUFFICIENT_SOURCES,
    RECENCY_STALE_DAYS,
    get_signals,
)

# Bump whenever a matching rule (app.services.decision_matching), an
# evidence-classification rule, or a threshold in this module changes, so a
# persisted DecisionEvaluation always identifies exactly which logic
# produced it, independent of how signal_version/clustering_version evolve
# afterward. Deliberately covers both matching and evidence-classification
# logic as a single Phase 4A version, rather than two separately-versioned
# pieces — they always ship and change together in this phase.
DECISION_EVALUATION_VERSION = "decision-evidence-v1"

# --- Confidence gating -------------------------------------------------------
# Core principle: the same weak signal is COUNTER evidence when there's
# enough volume to trust the measurement, and a GAP when there isn't enough
# volume to know. Below this many linked feedback items, concentration-type
# signals (source concentration, customer reach) are not trusted enough to
# call counter-evidence — they become explicit gaps instead. E.g. 2 items
# from one source is insufficient data; 10 items from one source is a real
# observed concentration. Unvalidated hackathon-scale constant.
MIN_VOLUME_FOR_JUDGEMENT = 5

HIGH_FREQUENCY_THRESHOLD = 5
# Fraction of *analyzed* members whose severity falls in the high/critical
# buckets before frequency of severe reports counts as supporting evidence.
HIGH_SEVERITY_FRACTION_THRESHOLD = 0.5
MIN_CUSTOMER_REACH_THRESHOLD = 2


def _fact(code: str, label: str, value, threshold, source_signal_path: str | None) -> dict:
    return {
        "code": code,
        "label": label,
        "value": value,
        "threshold": threshold,
        "source_signal_path": source_signal_path,
    }


# --- Supporting rules ---------------------------------------------------------


def _supporting_facts(snapshot: ProductIssueSignalSnapshot) -> list[dict]:
    facts = []

    if snapshot.feedback_count >= HIGH_FREQUENCY_THRESHOLD:
        facts.append(
            _fact(
                "high_frequency",
                "High feedback frequency",
                {"feedback_count": snapshot.feedback_count},
                HIGH_FREQUENCY_THRESHOLD,
                "feedback_count",
            )
        )

    analyzed_count = snapshot.severity["analyzed_count"]
    if analyzed_count > 0:
        buckets = snapshot.severity["buckets"]
        severe_count = buckets.get("high", 0) + buckets.get("critical", 0)
        severe_fraction = severe_count / analyzed_count
        if severe_fraction >= HIGH_SEVERITY_FRACTION_THRESHOLD:
            facts.append(
                _fact(
                    "high_severity_concentration",
                    "High/critical severity dominates analyzed feedback",
                    {
                        "severe_count": severe_count,
                        "analyzed_count": analyzed_count,
                        "severe_fraction": round(severe_fraction, 4),
                    },
                    HIGH_SEVERITY_FRACTION_THRESHOLD,
                    "severity.buckets",
                )
            )

    trend = snapshot.trend
    if trend["sufficient_data"] and trend["direction"] == "increasing":
        facts.append(
            _fact(
                "increasing_trend",
                "Increasing recent feedback",
                {
                    "direction": "increasing",
                    "current_window_count": trend["current_window_count"],
                    "previous_window_count": trend["previous_window_count"],
                },
                None,
                "trend.direction",
            )
        )

    tier_distribution = snapshot.segment_coverage["customer_tier_distribution"]
    if len(tier_distribution) >= 2:
        facts.append(
            _fact(
                "multiple_customer_segments",
                "Multiple distinct customer segments affected",
                {"customer_tier_distribution": tier_distribution},
                2,
                "segment_coverage.customer_tier_distribution",
            )
        )

    if snapshot.evidence_quality["source_diversity_sufficient"]:
        facts.append(
            _fact(
                "multiple_independent_sources",
                "Reported through multiple independent sources",
                {
                    "distinct_sources": snapshot.source_diversity["distinct_sources"],
                    "source_distribution": snapshot.source_diversity["source_distribution"],
                },
                MIN_SUFFICIENT_SOURCES,
                "evidence_quality.source_diversity_sufficient",
            )
        )

    distinct_known_customers = snapshot.customer_reach["distinct_known_customers"]
    if distinct_known_customers >= MIN_CUSTOMER_REACH_THRESHOLD:
        facts.append(
            _fact(
                "broad_known_customer_reach",
                "Affects multiple known customers",
                {"distinct_known_customers": distinct_known_customers},
                MIN_CUSTOMER_REACH_THRESHOLD,
                "customer_reach.distinct_known_customers",
            )
        )

    if snapshot.evidence_quality["recency"] == "recent":
        facts.append(
            _fact(
                "sustained_recency",
                "Recently reported",
                {"recency": "recent", "days_since_latest": snapshot.recency["days_since_latest"]},
                RECENCY_STALE_DAYS,
                "evidence_quality.recency",
            )
        )

    return facts


# --- Counter rules -------------------------------------------------------------
# Every rule here that concerns concentration/reach is gated by
# MIN_VOLUME_FOR_JUDGEMENT — see the module docstring's confidence-gating
# principle. Counter-evidence never means "positive feedback exists" (this
# schema cannot represent that); it only ever means the case for this issue
# is weaker or less reliable than it first appears.


def _counter_facts(snapshot: ProductIssueSignalSnapshot) -> list[dict]:
    facts = []
    feedback_count = snapshot.feedback_count

    if feedback_count >= MIN_VOLUME_FOR_JUDGEMENT and snapshot.source_diversity["distinct_sources"] == 1:
        facts.append(
            _fact(
                "single_source_concentration",
                "All evidence concentrated in a single source",
                {
                    "distinct_sources": 1,
                    "source_distribution": snapshot.source_diversity["source_distribution"],
                    "feedback_count": feedback_count,
                },
                MIN_VOLUME_FOR_JUDGEMENT,
                "source_diversity.distinct_sources",
            )
        )

    if snapshot.evidence_quality["duplicate_concentration"] == "high":
        facts.append(
            _fact(
                "high_duplicate_concentration",
                "High concentration of duplicate relationships",
                {
                    "duplicate_ratio": snapshot.duplicate_ratio["duplicate_ratio"],
                    "duplicate_pair_count": snapshot.duplicate_ratio["duplicate_pair_count"],
                    "related_pair_count": snapshot.duplicate_ratio["related_pair_count"],
                },
                DUPLICATE_CONCENTRATION_MODERATE_MAX,
                "evidence_quality.duplicate_concentration",
            )
        )

    if snapshot.evidence_quality["recency"] == "stale":
        facts.append(
            _fact(
                "stale_evidence",
                "Evidence is stale",
                {"recency": "stale", "days_since_latest": snapshot.recency["days_since_latest"]},
                RECENCY_STALE_DAYS,
                "evidence_quality.recency",
            )
        )

    if feedback_count >= MIN_VOLUME_FOR_JUDGEMENT and snapshot.customer_reach["distinct_known_customers"] == 0:
        facts.append(
            _fact(
                "low_known_customer_reach",
                "No known customers identified despite sufficient volume",
                {
                    "distinct_known_customers": 0,
                    "anonymous_feedback_count": snapshot.customer_reach["anonymous_feedback_count"],
                    "feedback_count": feedback_count,
                },
                MIN_VOLUME_FOR_JUDGEMENT,
                "customer_reach.distinct_known_customers",
            )
        )

    trend = snapshot.trend
    # Flat trend is deliberately NOT counter-evidence — steady isn't
    # declining. Only a sufficiently-sampled decrease counts.
    if trend["sufficient_data"] and trend["direction"] == "decreasing":
        facts.append(
            _fact(
                "declining_trend",
                "Declining recent feedback",
                {
                    "direction": "decreasing",
                    "current_window_count": trend["current_window_count"],
                    "previous_window_count": trend["previous_window_count"],
                },
                None,
                "trend.direction",
            )
        )

    return facts


# --- Gap rules -----------------------------------------------------------------
# Missing/insufficient evidence, never negative evidence.


def _gap_facts(snapshot: ProductIssueSignalSnapshot) -> list[dict]:
    facts = []
    feedback_count = snapshot.feedback_count

    trend = snapshot.trend
    if not trend["sufficient_data"]:
        facts.append(
            _fact(
                "insufficient_recent_feedback",
                "Not enough recent feedback to assess a trend",
                {
                    "current_window_count": trend["current_window_count"],
                    "previous_window_count": trend["previous_window_count"],
                    "min_sample_required": trend["min_sample_required"],
                },
                trend["min_sample_required"],
                "trend.sufficient_data",
            )
        )

    if feedback_count < MIN_VOLUME_FOR_JUDGEMENT:
        facts.append(
            _fact(
                "insufficient_source_diversity_data",
                "Not enough feedback volume to judge source diversity",
                {"feedback_count": feedback_count, "distinct_sources": snapshot.source_diversity["distinct_sources"]},
                MIN_VOLUME_FOR_JUDGEMENT,
                "source_diversity.distinct_sources",
            )
        )
        facts.append(
            _fact(
                "low_volume_overall",
                "Overall feedback volume is low",
                {"feedback_count": feedback_count},
                MIN_VOLUME_FOR_JUDGEMENT,
                "feedback_count",
            )
        )

    if feedback_count > 0 and snapshot.segment_coverage["unknown_tier_count"] == feedback_count:
        facts.append(
            _fact(
                "missing_customer_segmentation",
                "No customer segment (tier) data available",
                {"unknown_tier_count": snapshot.segment_coverage["unknown_tier_count"], "feedback_count": feedback_count},
                None,
                "segment_coverage.unknown_tier_count",
            )
        )

    if feedback_count > 0 and snapshot.severity["analyzed_count"] == 0:
        facts.append(
            _fact(
                "severity_not_analyzed",
                "No severity analysis available for this issue's feedback yet",
                {"analyzed_count": 0, "feedback_count": feedback_count},
                None,
                "severity.analyzed_count",
            )
        )

    return facts


def _no_signal_snapshot_gap(product_issue_id: uuid.UUID) -> dict:
    return _fact(
        "no_signal_snapshot",
        "No signals have been calculated for this issue yet",
        {"product_issue_id": str(product_issue_id)},
        None,
        "product_issue_signal_snapshot",
    )


def _volume_summary(snapshot: ProductIssueSignalSnapshot) -> dict:
    """A rough, explicitly-approximate deduplicated volume estimate
    (feedback_count minus internal duplicate pairs, floored at 0) — kept
    separate from feedback_count so a reader never conflates raw volume with
    distinct signal count when duplicate concentration is high."""
    duplicate_pair_count = snapshot.duplicate_ratio["duplicate_pair_count"]
    return {
        "feedback_count": snapshot.feedback_count,
        "duplicate_pair_count": duplicate_pair_count,
        "related_pair_count": snapshot.duplicate_ratio["related_pair_count"],
        "distinct_signal_estimate": max(0, snapshot.feedback_count - duplicate_pair_count),
    }


def evaluate_decision(
    db: Session, decision: Decision, *, reference_time: datetime | None = None
) -> DecisionEvaluation:
    """Runs deterministic matching + evidence classification for `decision`
    and appends a new DecisionEvaluation (never overwrites a previous one —
    see DecisionEvaluation's docstring). No LLM call, no signal
    recalculation: this reads whatever ProductIssueSignalSnapshot rows
    currently exist (Phase 3C's job) and classifies them.
    """
    reference_time = reference_time or datetime.now(timezone.utc)

    candidates = find_matching_issues(db, decision)

    decision_level_gaps = []
    if not candidates:
        decision_level_gaps.append(
            _fact(
                "no_matching_issues_found",
                "No ProductIssues matched this decision's keywords",
                {"keywords": decision.keywords},
                None,
                None,
            )
        )

    evaluation = DecisionEvaluation(
        decision_id=decision.id,
        evaluation_version=DECISION_EVALUATION_VERSION,
        reference_at=reference_time,
        decision_level_gaps=decision_level_gaps,
    )
    db.add(evaluation)
    db.flush()  # assign evaluation.id before creating evidence rows

    for candidate in candidates:
        snapshot = get_signals(db, candidate.issue.id)

        if snapshot is None:
            supporting: list[dict] = []
            counter: list[dict] = []
            gaps = [_no_signal_snapshot_gap(candidate.issue.id)]
            volume_summary: dict = {}
            snapshot_id = None
        else:
            supporting = _supporting_facts(snapshot)
            counter = _counter_facts(snapshot)
            gaps = _gap_facts(snapshot)
            volume_summary = _volume_summary(snapshot)
            snapshot_id = snapshot.id

        db.add(
            DecisionIssueEvidence(
                decision_evaluation_id=evaluation.id,
                product_issue_id=candidate.issue.id,
                product_issue_signal_snapshot_id=snapshot_id,
                match_method=candidate.method,
                match_score=candidate.score,
                matched_terms=candidate.matched_terms,
                volume_summary=volume_summary,
                supporting=supporting,
                counter=counter,
                gaps=gaps,
            )
        )

    db.commit()
    db.refresh(evaluation)
    return evaluation


def get_evaluation(db: Session, evaluation_id: uuid.UUID) -> DecisionEvaluation | None:
    return db.get(DecisionEvaluation, evaluation_id)


def get_latest_evaluation(db: Session, decision_id: uuid.UUID) -> DecisionEvaluation | None:
    """Ordered by `reference_at` (computed in Python per call, via
    `datetime.now(timezone.utc)` when the caller doesn't override it) rather
    than `created_at` (a Postgres `now()` server default, which reflects
    transaction-start time — two evaluations committed in the same
    transaction, e.g. a backfill script, would otherwise tie). `id` is a
    final, purely-deterministic tiebreak for the pathological case of two
    evaluations sharing the exact same reference_at."""
    stmt = (
        select(DecisionEvaluation)
        .where(DecisionEvaluation.decision_id == decision_id)
        .order_by(DecisionEvaluation.reference_at.desc(), DecisionEvaluation.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def list_evaluations(
    db: Session, decision_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[DecisionEvaluation], int]:
    total_count = db.execute(
        select(func.count()).select_from(DecisionEvaluation).where(DecisionEvaluation.decision_id == decision_id)
    ).scalar_one()

    stmt = (
        select(DecisionEvaluation)
        .where(DecisionEvaluation.decision_id == decision_id)
        .order_by(DecisionEvaluation.reference_at.desc(), DecisionEvaluation.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = list(db.execute(stmt).scalars())
    return items, total_count


def get_evidence_for_evaluation(db: Session, evaluation_id: uuid.UUID) -> list[DecisionIssueEvidence]:
    stmt = select(DecisionIssueEvidence).where(DecisionIssueEvidence.decision_evaluation_id == evaluation_id)
    return list(db.execute(stmt).scalars())

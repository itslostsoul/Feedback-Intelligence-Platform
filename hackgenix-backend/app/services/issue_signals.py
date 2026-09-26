import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db.models.feedback import Feedback
from app.db.models.feedback_analysis import FeedbackAnalysis
from app.db.models.feedback_relationship import FeedbackRelationship
from app.db.models.product_issue import ProductIssue
from app.db.models.product_issue_feedback import ProductIssueFeedback
from app.db.models.product_issue_signal_snapshot import ProductIssueSignalSnapshot

# Bump whenever a formula, bucket boundary, or window definition below
# changes, so a persisted snapshot's `signal_version` always identifies
# exactly which logic produced it (reproducibility, matching the
# `clustering_version`/`algorithm_version` convention used elsewhere).
SIGNAL_ALGORITHM_VERSION = "signal-v1"

# --- Severity buckets (FeedbackAnalysis.severity is an integer 1-10) --------
SEVERITY_BUCKETS: list[tuple[str, int, int]] = [
    ("low", 1, 3),
    ("medium", 4, 6),
    ("high", 7, 8),
    ("critical", 9, 10),
]

# --- Trend window definition -------------------------------------------------
# Two adjacent, non-overlapping 7-day windows ending at the reference
# timestamp: "current" = [reference-7d, reference], "previous" =
# [reference-14d, reference-7d). A week is long enough to smooth out daily
# ingestion noise while still being short enough to be meaningful at
# hackathon/demo data volumes.
TREND_WINDOW_DAYS = 7

# Below this many combined items across both trend windows, a
# increasing/decreasing/flat call is not statistically meaningful — declared
# explicitly as "insufficient_data" rather than guessed.
MIN_TREND_SAMPLE = 4

# A feedback item is "recent" (evidence-quality recency flag) if its most
# recent linked item falls within this many days of the reference timestamp.
RECENCY_STALE_DAYS = 30

# Duplicate-ratio concentration thresholds (fraction of same-issue
# relationship pairs classified as DUPLICATE rather than RELATED).
DUPLICATE_CONCENTRATION_LOW_MAX = 1 / 3
DUPLICATE_CONCENTRATION_MODERATE_MAX = 2 / 3

# Minimum distinct sources for the evidence-quality "source diversity"
# component to be considered sufficient — a single source can't demonstrate
# the problem is broader than one channel's reporting bias.
MIN_SUFFICIENT_SOURCES = 2


def _effective_timestamp_column():
    """`created_at` (when the feedback happened at its source) when known,
    falling back to `ingested_at` (when we received it) — used consistently
    for every time-based signal (recency, trend, time distribution) so they
    stay mutually consistent."""
    return func.coalesce(Feedback.created_at, Feedback.ingested_at)


def _member_ids_subquery(product_issue_id: uuid.UUID):
    return select(ProductIssueFeedback.feedback_id).where(
        ProductIssueFeedback.product_issue_id == product_issue_id
    )


def _calculate_frequency(db: Session, product_issue_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(ProductIssueFeedback).where(
        ProductIssueFeedback.product_issue_id == product_issue_id
    )
    return db.execute(stmt).scalar_one()


def _calculate_severity(db: Session, product_issue_id: uuid.UUID) -> dict:
    member_ids = _member_ids_subquery(product_issue_id)

    distribution_stmt = (
        select(FeedbackAnalysis.severity, func.count())
        .where(FeedbackAnalysis.feedback_id.in_(member_ids))
        .group_by(FeedbackAnalysis.severity)
    )
    distribution = {str(severity): count for severity, count in db.execute(distribution_stmt).all()}

    stats_stmt = select(
        func.count(),
        func.avg(FeedbackAnalysis.severity),
        func.percentile_cont(0.5).within_group(FeedbackAnalysis.severity),
    ).where(FeedbackAnalysis.feedback_id.in_(member_ids))
    analyzed_count, mean_severity, median_severity = db.execute(stats_stmt).one()

    total_count = _calculate_frequency(db, product_issue_id)
    bucket_counts = {
        name: sum(count for value, count in distribution.items() if low <= int(value) <= high)
        for name, low, high in SEVERITY_BUCKETS
    }

    return {
        "distribution": distribution,
        "buckets": bucket_counts,
        "mean": round(float(mean_severity), 2) if mean_severity is not None else None,
        "median": float(median_severity) if median_severity is not None else None,
        "analyzed_count": analyzed_count,
        "unanalyzed_count": total_count - analyzed_count,
    }


def _calculate_recency(db: Session, product_issue_id: uuid.UUID, reference_time: datetime) -> dict:
    member_ids = _member_ids_subquery(product_issue_id)
    timestamp_col = _effective_timestamp_column()

    stmt = (
        select(func.min(timestamp_col), func.max(timestamp_col))
        .select_from(Feedback)
        .where(Feedback.id.in_(member_ids))
    )
    oldest, latest = db.execute(stmt).one()

    return {
        "reference_at": reference_time.isoformat(),
        "latest_feedback_at": latest.isoformat() if latest else None,
        "oldest_feedback_at": oldest.isoformat() if oldest else None,
        "days_since_latest": (reference_time - latest).days if latest else None,
        "days_since_oldest": (reference_time - oldest).days if oldest else None,
    }


def _calculate_trend(db: Session, product_issue_id: uuid.UUID, reference_time: datetime) -> dict:
    member_ids = _member_ids_subquery(product_issue_id)
    timestamp_col = _effective_timestamp_column()

    current_start = reference_time - timedelta(days=TREND_WINDOW_DAYS)
    previous_start = reference_time - timedelta(days=2 * TREND_WINDOW_DAYS)

    current_count_expr = func.count(case((timestamp_col > current_start, 1)))
    previous_count_expr = func.count(
        case((timestamp_col.between(previous_start, current_start), 1))
    )

    stmt = (
        select(current_count_expr, previous_count_expr)
        .select_from(Feedback)
        .where(Feedback.id.in_(member_ids))
        .where(timestamp_col <= reference_time)
    )
    current_window_count, previous_window_count = db.execute(stmt).one()

    total_sampled = current_window_count + previous_window_count
    sufficient_data = total_sampled >= MIN_TREND_SAMPLE

    if not sufficient_data:
        direction = "insufficient_data"
    elif current_window_count > previous_window_count:
        direction = "increasing"
    elif current_window_count < previous_window_count:
        direction = "decreasing"
    else:
        direction = "flat"

    return {
        "window_days": TREND_WINDOW_DAYS,
        "current_window_count": current_window_count,
        "previous_window_count": previous_window_count,
        "sufficient_data": sufficient_data,
        "min_sample_required": MIN_TREND_SAMPLE,
        "direction": direction,
    }


def _calculate_customer_reach(db: Session, product_issue_id: uuid.UUID) -> dict:
    member_ids = _member_ids_subquery(product_issue_id)

    stmt = select(
        func.count(func.distinct(Feedback.customer_id)),
        func.count(case((Feedback.customer_id.isnot(None), 1))),
        func.count(case((Feedback.customer_id.is_(None), 1))),
    ).where(Feedback.id.in_(member_ids))
    distinct_known_customers, known_feedback_count, anonymous_feedback_count = db.execute(stmt).one()

    return {
        "distinct_known_customers": distinct_known_customers,
        "known_feedback_count": known_feedback_count,
        "anonymous_feedback_count": anonymous_feedback_count,
        "note": "Anonymous feedback items are counted individually, never assumed to map to distinct people.",
    }


def _calculate_source_diversity(db: Session, product_issue_id: uuid.UUID) -> dict:
    member_ids = _member_ids_subquery(product_issue_id)

    stmt = (
        select(Feedback.source, func.count())
        .where(Feedback.id.in_(member_ids))
        .group_by(Feedback.source)
    )
    distribution = dict(db.execute(stmt).all())

    type_stmt = (
        select(Feedback.source_type, func.count())
        .where(Feedback.id.in_(member_ids))
        .group_by(Feedback.source_type)
    )
    type_distribution = {t.value: count for t, count in db.execute(type_stmt).all()}

    return {
        "distinct_sources": len(distribution),
        "source_distribution": distribution,
        "source_type_distribution": type_distribution,
    }


def _calculate_segment_coverage(db: Session, product_issue_id: uuid.UUID) -> dict:
    member_ids = _member_ids_subquery(product_issue_id)

    stmt = (
        select(Feedback.customer_tier, func.count())
        .where(Feedback.id.in_(member_ids))
        .group_by(Feedback.customer_tier)
    )
    rows = db.execute(stmt).all()

    tier_distribution = {tier: count for tier, count in rows if tier is not None}
    unknown_tier_count = sum(count for tier, count in rows if tier is None)

    return {
        "customer_tier_distribution": tier_distribution,
        "unknown_tier_count": unknown_tier_count,
    }


def _calculate_duplicate_ratio(db: Session, product_issue_id: uuid.UUID, feedback_count: int) -> dict:
    """Only relationship rows where *both* endpoints are members of this
    issue are counted. `feedback_relationship` already stores each unordered
    pair exactly once (feedback_id_a < feedback_id_b is enforced by a CHECK
    constraint), so there is no double-counting to guard against here."""
    member_ids = _member_ids_subquery(product_issue_id)

    stmt = (
        select(FeedbackRelationship.relationship_type, func.count())
        .where(FeedbackRelationship.feedback_id_a.in_(member_ids))
        .where(FeedbackRelationship.feedback_id_b.in_(member_ids))
        .group_by(FeedbackRelationship.relationship_type)
    )
    counts = {rel_type.value: count for rel_type, count in db.execute(stmt).all()}
    duplicate_count = counts.get("duplicate", 0)
    related_count = counts.get("related", 0)
    total_pairs = duplicate_count + related_count
    possible_pairs = feedback_count * (feedback_count - 1) // 2

    return {
        "duplicate_pair_count": duplicate_count,
        "related_pair_count": related_count,
        "total_relationship_pairs": total_pairs,
        "possible_pairs": possible_pairs,
        "duplicate_ratio": round(duplicate_count / total_pairs, 4) if total_pairs > 0 else None,
        "relationship_coverage": round(total_pairs / possible_pairs, 4) if possible_pairs > 0 else None,
    }


def _duplicate_concentration_flag(duplicate_ratio: float | None) -> str:
    if duplicate_ratio is None:
        return "insufficient_data"
    if duplicate_ratio <= DUPLICATE_CONCENTRATION_LOW_MAX:
        return "low"
    if duplicate_ratio <= DUPLICATE_CONCENTRATION_MODERATE_MAX:
        return "moderate"
    return "high"


def _calculate_time_distribution(db: Session, product_issue_id: uuid.UUID) -> dict:
    """Sparse weekly histogram (ISO week start date) of the effective
    timestamp — deterministic, and reusable as the basis for a future,
    richer trend analysis. Only weeks with at least one item are included.

    `date_trunc` buckets according to the *session* timezone, which is not
    guaranteed to be UTC (this deployment's Postgres session defaults to
    Asia/Calcutta) — bucketing would otherwise silently shift by whatever
    offset the session happens to be in, breaking reproducibility across
    environments. `timezone('UTC', ...)` normalizes to UTC wall-clock first
    so week boundaries are fixed regardless of session/server configuration.
    """
    member_ids = _member_ids_subquery(product_issue_id)
    week_start = func.date_trunc("week", func.timezone("UTC", _effective_timestamp_column()))

    stmt = (
        select(week_start, func.count())
        .select_from(Feedback)
        .where(Feedback.id.in_(member_ids))
        .group_by(week_start)
        .order_by(week_start)
    )
    buckets = [
        {"period_start": period_start.date().isoformat(), "count": count}
        for period_start, count in db.execute(stmt).all()
    ]

    return {"granularity": "week", "buckets": buckets}


def _calculate_evidence_quality(
    *,
    severity: dict,
    recency: dict,
    customer_reach: dict,
    source_diversity: dict,
    segment_coverage: dict,
    duplicate_ratio: dict,
    reference_time: datetime,
) -> dict:
    """Transparent component flags only — deliberately not a blended score.
    Each component is independently inspectable; a consumer (or a future
    Decision Engine) decides how, or whether, to combine them."""
    days_since_latest = recency["days_since_latest"]
    if days_since_latest is None:
        recency_flag = "unknown"
    elif days_since_latest <= RECENCY_STALE_DAYS:
        recency_flag = "recent"
    else:
        recency_flag = "stale"

    return {
        "source_diversity_sufficient": source_diversity["distinct_sources"] >= MIN_SUFFICIENT_SOURCES,
        "customer_reach_known": customer_reach["distinct_known_customers"] > 0,
        "severity_data_available": severity["analyzed_count"] > 0,
        "severity_analyzed_fraction": (
            round(severity["analyzed_count"] / (severity["analyzed_count"] + severity["unanalyzed_count"]), 4)
            if (severity["analyzed_count"] + severity["unanalyzed_count"]) > 0
            else None
        ),
        "duplicate_concentration": _duplicate_concentration_flag(duplicate_ratio["duplicate_ratio"]),
        "recency": recency_flag,
        "segment_coverage_known": bool(segment_coverage["customer_tier_distribution"]),
        "evaluated_at": reference_time.isoformat(),
    }


def calculate_issue_signals(
    db: Session, issue: ProductIssue, *, reference_time: datetime | None = None
) -> ProductIssueSignalSnapshot:
    """Computes every Phase 3C signal for one ProductIssue from its current
    `product_issue_feedback` membership and persists a single upserted
    snapshot row. Never calls an LLM. Aggregation happens in PostgreSQL
    (COUNT/GROUP BY/percentile_cont) rather than loading member feedback rows
    into Python.

    `reference_time` defaults to the current UTC time; callers (tests) may
    pass an explicit value so recency/trend results are reproducible
    independent of wall-clock time.
    """
    reference_time = reference_time or datetime.now(timezone.utc)

    feedback_count = _calculate_frequency(db, issue.id)
    severity = _calculate_severity(db, issue.id)
    recency = _calculate_recency(db, issue.id, reference_time)
    trend = _calculate_trend(db, issue.id, reference_time)
    customer_reach = _calculate_customer_reach(db, issue.id)
    source_diversity = _calculate_source_diversity(db, issue.id)
    segment_coverage = _calculate_segment_coverage(db, issue.id)
    duplicate_ratio = _calculate_duplicate_ratio(db, issue.id, feedback_count)
    time_distribution = _calculate_time_distribution(db, issue.id)
    evidence_quality = _calculate_evidence_quality(
        severity=severity,
        recency=recency,
        customer_reach=customer_reach,
        source_diversity=source_diversity,
        segment_coverage=segment_coverage,
        duplicate_ratio=duplicate_ratio,
        reference_time=reference_time,
    )

    existing = db.execute(
        select(ProductIssueSignalSnapshot).where(
            ProductIssueSignalSnapshot.product_issue_id == issue.id
        )
    ).scalar_one_or_none()

    if existing is not None:
        snapshot = existing
    else:
        snapshot = ProductIssueSignalSnapshot(product_issue_id=issue.id)

    snapshot.signal_version = SIGNAL_ALGORITHM_VERSION
    snapshot.reference_at = reference_time
    snapshot.feedback_count = feedback_count
    snapshot.severity = severity
    snapshot.recency = recency
    snapshot.trend = trend
    snapshot.customer_reach = customer_reach
    snapshot.source_diversity = source_diversity
    snapshot.segment_coverage = segment_coverage
    snapshot.duplicate_ratio = duplicate_ratio
    snapshot.time_distribution = time_distribution
    snapshot.evidence_quality = evidence_quality

    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


def calculate_all_issue_signals(
    db: Session, *, reference_time: datetime | None = None
) -> list[ProductIssueSignalSnapshot]:
    """Recalculates signals for every ProductIssue. Each issue is committed
    independently (inside calculate_issue_signals) so one issue's failure
    doesn't roll back another's already-computed snapshot."""
    reference_time = reference_time or datetime.now(timezone.utc)
    issues = db.execute(select(ProductIssue)).scalars().all()
    return [calculate_issue_signals(db, issue, reference_time=reference_time) for issue in issues]


def get_signals(db: Session, issue_id: uuid.UUID) -> ProductIssueSignalSnapshot | None:
    return db.execute(
        select(ProductIssueSignalSnapshot).where(
            ProductIssueSignalSnapshot.product_issue_id == issue_id
        )
    ).scalar_one_or_none()

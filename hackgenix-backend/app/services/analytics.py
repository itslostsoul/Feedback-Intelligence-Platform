"""Deterministic, LLM-free analytics aggregation over already-persisted data.

Every function here reads only Feedback, FeedbackAnalysis, ProductIssue,
ProductIssueFeedback and ProductIssueSignalSnapshot — the same tables Phases
2/3A/3B-3/3C already populate — and computes its result in PostgreSQL
(COUNT/GROUP BY/date_trunc/percentile-style aggregation) rather than loading
every row into Python. Nothing here calls an LLM; the AI-generated
interpretation layered on top of this module's output lives in
app.services.analytics_insight.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db.models.enums import FeedbackCategory, ProductIssueStatus
from app.db.models.feedback import Feedback
from app.db.models.feedback_analysis import FeedbackAnalysis
from app.db.models.product_issue import ProductIssue
from app.db.models.product_issue_feedback import ProductIssueFeedback
from app.db.models.product_issue_signal_snapshot import ProductIssueSignalSnapshot
from app.schemas.analytics import (
    AnalyticsCategoriesResponse,
    AnalyticsOverview,
    AnalyticsSourcesResponse,
    AnalyticsTrendsResponse,
    CategoryCount,
    DailyTrendPoint,
    IssueTrendItem,
    IssueVolumeItem,
    RecentFeedbackTrend,
    SourceCount,
    TopIssueItem,
    TopIssuesResponse,
)

# Bump whenever a formula, threshold, or window definition below changes.
ANALYTICS_VERSION = "analytics-v1"

# A feedback item counts as "high severity" once it reaches the "high"
# bucket boundary already used by app.services.issue_signals.SEVERITY_BUCKETS
# ("high" starts at 7) — kept as its own constant here rather than importing
# the full per-issue bucket list, since analytics only ever needs this one
# threshold.
HIGH_SEVERITY_MIN = 7

# Same window/insufficient-sample convention as the per-issue trend in
# app.services.issue_signals, applied here across the whole feedback corpus.
TREND_WINDOW_DAYS = 7
MIN_TREND_SAMPLE = 4

# Below this many distinct calendar days with at least one feedback item, a
# day-over-day trend chart is not a reliable signal.
MIN_DAYS_FOR_RELIABLE_TREND = 3

ISSUE_RANKING_VERSION = "issue-ranking-v1"
ISSUE_RANKING_METHOD = (
    "Sorted by feedback_count (desc), then average_severity (desc, nulls last), "
    "then distinct_known_customers (desc, nulls last). A transparent multi-key "
    "sort over existing deterministic signals — never a blended score."
)


def _effective_timestamp_column():
    """Same fallback convention as app.services.issue_signals: `created_at`
    (when known) else `ingested_at`, used consistently for every time-based
    calculation below."""
    return func.coalesce(Feedback.created_at, Feedback.ingested_at)


def _feedback_totals(db: Session) -> tuple[int, int]:
    total = db.execute(select(func.count()).select_from(Feedback)).scalar_one()
    analyzed = db.execute(select(func.count()).select_from(FeedbackAnalysis)).scalar_one()
    return total, analyzed


def _severity_stats(db: Session) -> tuple[float | None, int]:
    (avg_severity,) = db.execute(select(func.avg(FeedbackAnalysis.severity))).one()
    high_count = db.execute(
        select(func.count()).select_from(FeedbackAnalysis).where(FeedbackAnalysis.severity >= HIGH_SEVERITY_MIN)
    ).scalar_one()
    return (round(float(avg_severity), 2) if avg_severity is not None else None), high_count


def _issue_totals(db: Session) -> tuple[int, int, int]:
    stmt = select(ProductIssue.status, func.count()).group_by(ProductIssue.status)
    counts = {status.value: count for status, count in db.execute(stmt).all()}
    total = sum(counts.values())
    return total, counts.get(ProductIssueStatus.OPEN.value, 0), counts.get(ProductIssueStatus.RESOLVED.value, 0)


def _unique_customers(db: Session) -> int:
    return db.execute(
        select(func.count(func.distinct(Feedback.customer_id))).where(Feedback.customer_id.isnot(None))
    ).scalar_one()


def _source_type_distribution(db: Session) -> dict[str, int]:
    """Grouped by the bounded `source_type` enum for a digestible overview
    widget — the raw per-`source` breakdown (unbounded cardinality) is what
    compute_sources()/GET /analytics/sources returns instead."""
    stmt = select(Feedback.source_type, func.count()).group_by(Feedback.source_type)
    return {source_type.value: count for source_type, count in db.execute(stmt).all()}


def _category_counts(db: Session) -> dict[str, int]:
    """Only categories with at least one analyzed item are present — see
    compute_categories() for the version that fills in the full known
    taxonomy with real zero counts."""
    stmt = select(FeedbackAnalysis.category, func.count()).group_by(FeedbackAnalysis.category)
    return {category.value: count for category, count in db.execute(stmt).all()}


def _issue_volume(db: Session) -> list[IssueVolumeItem]:
    stmt = (
        select(ProductIssue.id, ProductIssue.title, func.count(ProductIssueFeedback.feedback_id))
        .select_from(ProductIssue)
        .outerjoin(ProductIssueFeedback, ProductIssueFeedback.product_issue_id == ProductIssue.id)
        .group_by(ProductIssue.id, ProductIssue.title)
        .order_by(ProductIssue.created_at.desc())
    )
    return [
        IssueVolumeItem(issue_id=issue_id, title=title, feedback_count=count)
        for issue_id, title, count in db.execute(stmt).all()
    ]


def _recent_feedback_trend(db: Session, reference_time: datetime) -> RecentFeedbackTrend:
    timestamp_col = _effective_timestamp_column()
    current_start = reference_time - timedelta(days=TREND_WINDOW_DAYS)
    previous_start = reference_time - timedelta(days=2 * TREND_WINDOW_DAYS)

    current_count_expr = func.count(case((timestamp_col > current_start, 1)))
    previous_count_expr = func.count(case((timestamp_col.between(previous_start, current_start), 1)))

    stmt = (
        select(current_count_expr, previous_count_expr)
        .select_from(Feedback)
        .where(timestamp_col <= reference_time)
    )
    current_count, previous_count = db.execute(stmt).one()

    total_sampled = current_count + previous_count
    sufficient = total_sampled >= MIN_TREND_SAMPLE
    if not sufficient:
        direction = "insufficient_data"
    elif current_count > previous_count:
        direction = "increasing"
    elif current_count < previous_count:
        direction = "decreasing"
    else:
        direction = "flat"

    return RecentFeedbackTrend(
        window_days=TREND_WINDOW_DAYS,
        current_window_count=current_count,
        previous_window_count=previous_count,
        sufficient_data=sufficient,
        min_sample_required=MIN_TREND_SAMPLE,
        direction=direction,
    )


def _issue_trends(db: Session) -> list[IssueTrendItem]:
    """Reads each issue's already-persisted ProductIssueSignalSnapshot.trend
    — never recomputes it. Issues with no snapshot yet are omitted rather
    than backfilled with a guessed value."""
    stmt = select(ProductIssue.id, ProductIssue.title, ProductIssueSignalSnapshot.trend).join(
        ProductIssueSignalSnapshot, ProductIssueSignalSnapshot.product_issue_id == ProductIssue.id
    )
    return [
        IssueTrendItem(
            issue_id=issue_id,
            title=title,
            direction=trend["direction"],
            sufficient_data=trend["sufficient_data"],
            current_window_count=trend["current_window_count"],
            previous_window_count=trend["previous_window_count"],
        )
        for issue_id, title, trend in db.execute(stmt).all()
    ]


def _issue_metrics_rows(db: Session) -> list[TopIssueItem]:
    """One row per ProductIssue with its real-time feedback_count (same
    GROUP BY as _issue_volume) plus, where available, its persisted
    ProductIssueSignalSnapshot components. Loads the small, bounded set of
    ProductIssue/ProductIssueSignalSnapshot rows (never raw Feedback rows)
    to combine the two in Python — the same pattern
    app.services.issue_signals.calculate_all_issue_signals already uses."""
    count_stmt = select(ProductIssueFeedback.product_issue_id, func.count()).group_by(
        ProductIssueFeedback.product_issue_id
    )
    counts: dict[uuid.UUID, int] = dict(db.execute(count_stmt).all())

    issues = db.execute(select(ProductIssue)).scalars().all()
    snapshots = {s.product_issue_id: s for s in db.execute(select(ProductIssueSignalSnapshot)).scalars().all()}

    rows: list[TopIssueItem] = []
    for issue in issues:
        snapshot = snapshots.get(issue.id)
        feedback_count = counts.get(issue.id, 0)

        if snapshot is None:
            rows.append(
                TopIssueItem(
                    issue_id=issue.id,
                    title=issue.title,
                    category=issue.category,
                    affected_feature=issue.affected_feature,
                    status=issue.status,
                    feedback_count=feedback_count,
                    signals_calculated=False,
                    average_severity=None,
                    high_severity_count=None,
                    distinct_known_customers=None,
                    trend_direction=None,
                    duplicate_ratio=None,
                    distinct_sources=None,
                )
            )
            continue

        buckets = snapshot.severity.get("buckets", {})
        rows.append(
            TopIssueItem(
                issue_id=issue.id,
                title=issue.title,
                category=issue.category,
                affected_feature=issue.affected_feature,
                status=issue.status,
                feedback_count=feedback_count,
                signals_calculated=True,
                average_severity=snapshot.severity.get("mean"),
                high_severity_count=buckets.get("high", 0) + buckets.get("critical", 0),
                distinct_known_customers=snapshot.customer_reach.get("distinct_known_customers"),
                trend_direction=snapshot.trend.get("direction"),
                duplicate_ratio=snapshot.duplicate_ratio.get("duplicate_ratio"),
                distinct_sources=snapshot.source_diversity.get("distinct_sources"),
            )
        )
    return rows


def _ranking_sort_key(item: TopIssueItem) -> tuple[int, float, int]:
    return (
        item.feedback_count,
        item.average_severity if item.average_severity is not None else -1.0,
        item.distinct_known_customers if item.distinct_known_customers is not None else -1,
    )


def compute_top_issues(db: Session, *, limit: int) -> list[TopIssueItem]:
    """See ISSUE_RANKING_METHOD for the exact, documented sort order."""
    rows = _issue_metrics_rows(db)
    rows.sort(key=_ranking_sort_key, reverse=True)
    return rows[:limit]


def compute_top_issues_response(db: Session, *, limit: int) -> TopIssuesResponse:
    rows = _issue_metrics_rows(db)
    rows.sort(key=_ranking_sort_key, reverse=True)
    return TopIssuesResponse(
        items=rows[:limit],
        total=len(rows),
        ranking_version=ISSUE_RANKING_VERSION,
        ranking_method=ISSUE_RANKING_METHOD,
    )


def compute_overview(db: Session, *, reference_time: datetime | None = None) -> AnalyticsOverview:
    reference_time = reference_time or datetime.now(timezone.utc)

    total_feedback, analyzed_feedback = _feedback_totals(db)
    unanalyzed_feedback = total_feedback - analyzed_feedback
    analysis_coverage = round(analyzed_feedback / total_feedback, 4) if total_feedback > 0 else None

    total_issues, open_issues, resolved_issues = _issue_totals(db)
    average_severity, high_severity_count = _severity_stats(db)

    return AnalyticsOverview(
        total_feedback=total_feedback,
        analyzed_feedback=analyzed_feedback,
        unanalyzed_feedback=unanalyzed_feedback,
        analysis_coverage=analysis_coverage,
        total_product_issues=total_issues,
        open_product_issues=open_issues,
        resolved_product_issues=resolved_issues,
        average_severity=average_severity,
        high_severity_feedback_count=high_severity_count,
        unique_customers=_unique_customers(db),
        source_distribution=_source_type_distribution(db),
        category_distribution=_category_counts(db),
        issue_volume=_issue_volume(db),
        recent_feedback_trend=_recent_feedback_trend(db, reference_time),
        issue_trends=_issue_trends(db),
        top_issues=compute_top_issues(db, limit=5),
        calculated_at=reference_time,
    )


def compute_trends(db: Session, *, days: int, reference_time: datetime | None = None) -> AnalyticsTrendsResponse:
    """One point per calendar day over the trailing `days`-day window,
    computed via a single grouped SQL query then filled forward in Python
    for days with zero rows (a real, honest zero — never interpolated).
    `date_trunc` is normalized to UTC first for the same reproducibility
    reason as app.services.issue_signals._calculate_time_distribution."""
    reference_time = reference_time or datetime.now(timezone.utc)
    timestamp_col = _effective_timestamp_column()
    day_col = func.date_trunc("day", func.timezone("UTC", timestamp_col))

    # The window's lower bound must be *midnight* of the first day, not
    # `reference_time` shifted back by whole days at today's current
    # wall-clock time — the latter would (for e.g. days=1) put window_start
    # only moments before "now", silently excluding same-day feedback
    # ingested earlier today.
    first_day = reference_time.date() - timedelta(days=days - 1)
    window_start = datetime.combine(first_day, datetime.min.time(), tzinfo=timezone.utc)

    stmt = (
        select(
            day_col,
            func.count(),
            func.count(FeedbackAnalysis.id),
            func.count(case((FeedbackAnalysis.severity >= HIGH_SEVERITY_MIN, 1))),
        )
        .select_from(Feedback)
        .outerjoin(FeedbackAnalysis, FeedbackAnalysis.feedback_id == Feedback.id)
        .where(timestamp_col >= window_start)
        .where(timestamp_col <= reference_time)
        .group_by(day_col)
    )
    by_day = {day.date(): (count, analyzed, high) for day, count, analyzed, high in db.execute(stmt).all()}

    points: list[DailyTrendPoint] = []
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        count, analyzed, high = by_day.get(day, (0, 0, 0))
        points.append(
            DailyTrendPoint(
                date=day.isoformat(), feedback_count=count, analyzed_count=analyzed, high_severity_count=high
            )
        )

    earliest = db.execute(select(func.min(timestamp_col))).scalar_one()
    days_with_data = sum(1 for p in points if p.feedback_count > 0)
    sufficient_history = days_with_data >= MIN_DAYS_FOR_RELIABLE_TREND

    return AnalyticsTrendsResponse(
        window_days=days,
        points=points,
        earliest_feedback_at=earliest,
        days_with_data=days_with_data,
        sufficient_history=sufficient_history,
        min_days_for_reliable_trend=MIN_DAYS_FOR_RELIABLE_TREND,
        message=None if sufficient_history else "Not enough historical feedback to establish a reliable trend.",
    )


def compute_categories(db: Session) -> AnalyticsCategoriesResponse:
    """Always returns every known FeedbackCategory, with a real (possibly
    zero) count — never omits a category, and never fabricates a nonzero
    one."""
    counts = _category_counts(db)
    total_analyzed = sum(counts.values())
    items = [
        CategoryCount(
            category=category.value,
            count=counts.get(category.value, 0),
            percentage=(
                round(counts.get(category.value, 0) / total_analyzed * 100, 2) if total_analyzed > 0 else None
            ),
        )
        for category in FeedbackCategory
    ]
    return AnalyticsCategoriesResponse(total_analyzed=total_analyzed, items=items)


def compute_sources(db: Session) -> AnalyticsSourcesResponse:
    total_feedback = db.execute(select(func.count()).select_from(Feedback)).scalar_one()

    stmt = (
        select(Feedback.source, func.count(), func.count(FeedbackAnalysis.id))
        .select_from(Feedback)
        .outerjoin(FeedbackAnalysis, FeedbackAnalysis.feedback_id == Feedback.id)
        .group_by(Feedback.source)
        .order_by(func.count().desc())
    )
    items = [
        SourceCount(
            source=source,
            feedback_count=count,
            analyzed_count=analyzed,
            percentage=round(count / total_feedback * 100, 2) if total_feedback > 0 else 0.0,
        )
        for source, count, analyzed in db.execute(stmt).all()
    ]
    return AnalyticsSourcesResponse(total_feedback=total_feedback, items=items)

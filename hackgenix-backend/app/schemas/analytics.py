import uuid
from datetime import datetime

from pydantic import BaseModel

from app.db.models.enums import FeedbackCategory, ProductIssueStatus

# --- Overview -----------------------------------------------------------------


class RecentFeedbackTrend(BaseModel):
    """Whole-corpus version of the same current-vs-previous-window trend
    concept already used per-issue in app.services.issue_signals — same
    window length, same insufficient-data semantics, just measured across
    all feedback rather than one issue's members."""

    window_days: int
    current_window_count: int
    previous_window_count: int
    sufficient_data: bool
    min_sample_required: int
    direction: str


class IssueVolumeItem(BaseModel):
    issue_id: uuid.UUID
    title: str
    feedback_count: int


class IssueTrendItem(BaseModel):
    """Read directly from each issue's persisted ProductIssueSignalSnapshot.trend
    — never recomputed here. Issues with no snapshot yet are omitted rather
    than backfilled with a guessed value."""

    issue_id: uuid.UUID
    title: str
    direction: str
    sufficient_data: bool
    current_window_count: int
    previous_window_count: int


class TopIssueItem(BaseModel):
    """Every component metric behind an issue's ranking, so a caller can
    explain *why* it's significant instead of trusting an opaque score. See
    ISSUE_RANKING_VERSION in app.services.analytics for the exact, documented
    sort order this list is returned in — there is no blended/AI score
    anywhere in this shape."""

    issue_id: uuid.UUID
    title: str
    category: FeedbackCategory | None
    affected_feature: str | None
    status: ProductIssueStatus
    feedback_count: int
    signals_calculated: bool
    average_severity: float | None
    high_severity_count: int | None
    distinct_known_customers: int | None
    trend_direction: str | None
    duplicate_ratio: float | None
    distinct_sources: int | None


class TopIssuesResponse(BaseModel):
    items: list[TopIssueItem]
    total: int
    ranking_version: str
    ranking_method: str


class AnalyticsOverview(BaseModel):
    total_feedback: int
    analyzed_feedback: int
    unanalyzed_feedback: int
    analysis_coverage: float | None

    total_product_issues: int
    open_product_issues: int
    resolved_product_issues: int

    average_severity: float | None
    high_severity_feedback_count: int
    unique_customers: int

    source_distribution: dict[str, int]
    category_distribution: dict[str, int]
    issue_volume: list[IssueVolumeItem]
    recent_feedback_trend: RecentFeedbackTrend
    issue_trends: list[IssueTrendItem]
    top_issues: list[TopIssueItem]

    calculated_at: datetime


# --- Trends ---------------------------------------------------------------------


class DailyTrendPoint(BaseModel):
    date: str
    feedback_count: int
    analyzed_count: int
    high_severity_count: int


class AnalyticsTrendsResponse(BaseModel):
    window_days: int
    points: list[DailyTrendPoint]
    earliest_feedback_at: datetime | None
    days_with_data: int
    sufficient_history: bool
    min_days_for_reliable_trend: int
    message: str | None


# --- Categories -------------------------------------------------------------


class CategoryCount(BaseModel):
    category: str
    count: int
    percentage: float | None


class AnalyticsCategoriesResponse(BaseModel):
    total_analyzed: int
    items: list[CategoryCount]


# --- Sources ------------------------------------------------------------------


class SourceCount(BaseModel):
    source: str
    feedback_count: int
    analyzed_count: int
    percentage: float


class AnalyticsSourcesResponse(BaseModel):
    total_feedback: int
    items: list[SourceCount]

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_llm_provider
from app.db.database import get_db
from app.schemas.analytics import (
    AnalyticsCategoriesResponse,
    AnalyticsOverview,
    AnalyticsSourcesResponse,
    AnalyticsTrendsResponse,
    TopIssuesResponse,
)
from app.schemas.analytics_insight import AnalyticsInsightResult
from app.services.analytics import (
    compute_categories,
    compute_overview,
    compute_sources,
    compute_top_issues_response,
    compute_trends,
)
from app.services.analytics_insight import generate_insights
from app.services.llm.base import LLMProvider

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview", response_model=AnalyticsOverview)
def get_analytics_overview(db: Session = Depends(get_db)) -> AnalyticsOverview:
    """Deterministic, LLM-free snapshot across the whole database — see
    app.services.analytics for how every field is computed in PostgreSQL."""
    return compute_overview(db)


@router.get("/trends", response_model=AnalyticsTrendsResponse)
def get_analytics_trends(
    days: int = Query(default=30, ge=1, le=180),
    db: Session = Depends(get_db),
) -> AnalyticsTrendsResponse:
    """One point per calendar day for the trailing `days`-day window.
    `sufficient_history=False` (with `message` set) rather than a misleading
    flat/interpolated line when there isn't enough real history yet."""
    return compute_trends(db, days=days)


@router.get("/categories", response_model=AnalyticsCategoriesResponse)
def get_analytics_categories(db: Session = Depends(get_db)) -> AnalyticsCategoriesResponse:
    """Real counts (including honest zeros) for every known FeedbackCategory,
    from FeedbackAnalysis — never a fabricated category or count."""
    return compute_categories(db)


@router.get("/sources", response_model=AnalyticsSourcesResponse)
def get_analytics_sources(db: Session = Depends(get_db)) -> AnalyticsSourcesResponse:
    """Real per-source counts from Feedback.source, left-joined to
    FeedbackAnalysis for the analyzed count."""
    return compute_sources(db)


@router.get("/issues", response_model=TopIssuesResponse)
def get_analytics_issues(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> TopIssuesResponse:
    """The most significant product issues by a transparent, documented,
    deterministic multi-key sort over existing signals — see
    TopIssuesResponse.ranking_method. Never an AI/priority score."""
    return compute_top_issues_response(db, limit=limit)


@router.post("/insights", response_model=AnalyticsInsightResult)
def post_analytics_insights(
    db: Session = Depends(get_db),
    provider: LLMProvider = Depends(get_llm_provider),
) -> AnalyticsInsightResult:
    """Grounded LLM interpretation of a deterministic analytics evidence
    bundle (see app.services.analytics_insight) — the LLM never sees raw
    feedback text, never computes its own numbers, and every claim it
    returns must cite a real evidence_id or the whole generation is
    rejected. On provider failure, an AppError propagates to the existing
    global error handler; the deterministic endpoints above are entirely
    unaffected by this endpoint failing."""
    return generate_insights(db, provider)

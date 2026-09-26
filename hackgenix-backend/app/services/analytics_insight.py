"""AI Analytics Insight: a grounded LLM *interpretation* of the deterministic
analytics bundle in app.services.analytics — never a decision-maker, never a
second source of numbers. Same grounding philosophy as
app.services.decision_brief: the LLM only ever sees an already-computed
evidence bundle (never raw feedback text), and every claim it returns must
cite a real evidence_id from that bundle or the whole generation is rejected.

Deliberately stateless — no AnalyticsInsight table exists. Each call builds a
fresh bundle from the current database and returns the result directly; there
is no history to keep, matching this feature's "don't overbuild" scope.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import LLMOutputValidationError, LLMProviderError, LLMUngroundedOutputError
from app.schemas.analytics_insight import (
    AnalyticsEvidenceFact,
    AnalyticsInsightLLMOutput,
    AnalyticsInsightResult,
    AnalyticsSynthesisInput,
)
from app.services.analytics import compute_categories, compute_overview, compute_sources, compute_top_issues
from app.services.llm.base import LLMProvider

logger = logging.getLogger(__name__)

# Bump whenever the prompt text, the bundle-building logic below, or the
# grounding rules change.
INSIGHT_SYNTHESIS_VERSION = "analytics-insight-v1"

# How many of the top-ranked issues (app.services.analytics.compute_top_issues)
# get their own citable facts in the bundle — bounded so the prompt stays a
# manageable size rather than growing with the whole product-issue table.
TOP_ISSUES_FOR_INSIGHT = 8


def build_synthesis_input(db: Session, *, reference_time: datetime | None = None) -> AnalyticsSynthesisInput:
    """Builds the complete, deterministic LLM input purely from
    app.services.analytics's own aggregate functions — no LLM call, no new
    calculation, no raw feedback access. Same database state always produces
    the same bundle."""
    reference_time = reference_time or datetime.now(timezone.utc)
    overview = compute_overview(db, reference_time=reference_time)
    categories = compute_categories(db)
    sources = compute_sources(db)
    top_issues = compute_top_issues(db, limit=TOP_ISSUES_FOR_INSIGHT)

    facts: list[AnalyticsEvidenceFact] = [
        AnalyticsEvidenceFact(
            citation_id="analytics:feedback_volume",
            label="Total feedback ingested",
            value=overview.total_feedback,
        ),
        AnalyticsEvidenceFact(
            citation_id="analytics:analysis_coverage",
            label="Share of feedback that has completed AI analysis",
            value=overview.analysis_coverage,
        ),
        AnalyticsEvidenceFact(
            citation_id="analytics:severity:average",
            label="Average severity across analyzed feedback (1-10 scale)",
            value=overview.average_severity,
        ),
        AnalyticsEvidenceFact(
            citation_id="analytics:severity:high_count",
            label="Feedback items with high or critical severity (7 or above)",
            value=overview.high_severity_feedback_count,
        ),
        AnalyticsEvidenceFact(
            citation_id="analytics:customers:unique",
            label="Distinct known customers who have given feedback",
            value=overview.unique_customers,
        ),
        AnalyticsEvidenceFact(
            citation_id="analytics:issues:open_count",
            label="Open product issues",
            value=overview.open_product_issues,
        ),
        AnalyticsEvidenceFact(
            citation_id="analytics:issues:resolved_count",
            label="Resolved product issues",
            value=overview.resolved_product_issues,
        ),
        AnalyticsEvidenceFact(
            citation_id="analytics:trend:recent",
            label=(
                f"Whole-corpus feedback volume trend, last {overview.recent_feedback_trend.window_days} days "
                f"vs. the previous {overview.recent_feedback_trend.window_days}"
            ),
            value={
                "direction": overview.recent_feedback_trend.direction,
                "current_window_count": overview.recent_feedback_trend.current_window_count,
                "previous_window_count": overview.recent_feedback_trend.previous_window_count,
                "sufficient_data": overview.recent_feedback_trend.sufficient_data,
            },
        ),
    ]

    for cat in categories.items:
        facts.append(
            AnalyticsEvidenceFact(
                citation_id=f"analytics:category:{cat.category}",
                label=f"Feedback categorized as '{cat.category}'",
                value={"count": cat.count, "percentage_of_analyzed": cat.percentage},
            )
        )

    for src in sources.items:
        facts.append(
            AnalyticsEvidenceFact(
                citation_id=f"analytics:source:{src.source}",
                label=f"Feedback from source '{src.source}'",
                value={
                    "feedback_count": src.feedback_count,
                    "analyzed_count": src.analyzed_count,
                    "percentage": src.percentage,
                },
            )
        )

    for issue in top_issues:
        facts.append(
            AnalyticsEvidenceFact(
                citation_id=f"analytics:issue:{issue.issue_id}:feedback_count",
                label=f"Feedback count for product issue '{issue.title}'",
                value=issue.feedback_count,
            )
        )
        if not issue.signals_calculated:
            continue
        facts.append(
            AnalyticsEvidenceFact(
                citation_id=f"analytics:issue:{issue.issue_id}:trend",
                label=f"Trend direction for product issue '{issue.title}'",
                value=issue.trend_direction,
            )
        )
        if issue.average_severity is not None:
            facts.append(
                AnalyticsEvidenceFact(
                    citation_id=f"analytics:issue:{issue.issue_id}:severity",
                    label=f"Average severity for product issue '{issue.title}'",
                    value=issue.average_severity,
                )
            )
        if issue.distinct_known_customers is not None:
            facts.append(
                AnalyticsEvidenceFact(
                    citation_id=f"analytics:issue:{issue.issue_id}:customers",
                    label=f"Distinct known customers for product issue '{issue.title}'",
                    value=issue.distinct_known_customers,
                )
            )
        if issue.duplicate_ratio is not None:
            facts.append(
                AnalyticsEvidenceFact(
                    citation_id=f"analytics:issue:{issue.issue_id}:duplicate_ratio",
                    label=f"Duplicate-report ratio for product issue '{issue.title}'",
                    value=issue.duplicate_ratio,
                )
            )
        if issue.distinct_sources is not None:
            facts.append(
                AnalyticsEvidenceFact(
                    citation_id=f"analytics:issue:{issue.issue_id}:source_diversity",
                    label=f"Distinct sources reporting product issue '{issue.title}'",
                    value=issue.distinct_sources,
                )
            )

    return AnalyticsSynthesisInput(generated_from_calculated_at=reference_time, facts=facts)


def _first_unknown_evidence_id(
    output: AnalyticsInsightLLMOutput, registry: dict[str, AnalyticsEvidenceFact]
) -> str | None:
    for claim in (*output.key_findings, *output.emerging_patterns, *output.areas_to_investigate, *output.caveats):
        for evidence_id in claim.evidence_ids:
            if evidence_id not in registry:
                return evidence_id
    return None


def generate_insights(db: Session, provider: LLMProvider, settings: Settings | None = None) -> AnalyticsInsightResult:
    """Runs one grounded insight-generation attempt against the current
    database state. On provider failure, malformed output, or an ungrounded
    evidence_id, the exception propagates to the existing global AppError
    handler — this function never fabricates a fallback response; the caller
    (frontend) is expected to keep showing the deterministic analytics that
    are unaffected by this failing."""
    settings = settings or get_settings()
    synthesis_input = build_synthesis_input(db)
    registry = synthesis_input.citation_registry()
    generated_at = datetime.now(timezone.utc)

    try:
        output = provider.synthesize_analytics_insights(synthesis_input)
    except (LLMProviderError, LLMOutputValidationError) as exc:
        logger.warning("analytics_insight_generation_failed error=%s", type(exc).__name__)
        raise

    unknown_evidence_id = _first_unknown_evidence_id(output, registry)
    if unknown_evidence_id is not None:
        logger.warning("analytics_insight_ungrounded_evidence_id evidence_id=%s", unknown_evidence_id)
        raise LLMUngroundedOutputError(f"The LLM provider cited an unknown evidence_id: {unknown_evidence_id!r}.")

    return AnalyticsInsightResult(
        headline=output.headline,
        key_findings=output.key_findings,
        emerging_patterns=output.emerging_patterns,
        areas_to_investigate=output.areas_to_investigate,
        caveats=output.caveats,
        provider=settings.LLM_PROVIDER,
        model=settings.LLM_MODEL,
        insight_version=INSIGHT_SYNTHESIS_VERSION,
        generated_at=generated_at,
        evidence=registry,
    )

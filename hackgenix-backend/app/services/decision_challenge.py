import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.product_issue import ProductIssue
from app.schemas.decision_challenge import ChallengeIssueSummary, DecisionChallengeResult, IssueChallengeSummary
from app.schemas.decision_stability import ChangeEvent, ChangeEventType, DecisionStabilityResult

# Which ChangeEventTypes belong in each categorized bucket. Purely a
# regrouping of Phase 4C's own vocabulary — no new event types, no new
# classification logic. A given event appears in exactly one category
# bucket (plus always in `all_events`).
_SUPPORTING_TYPES = {ChangeEventType.SUPPORTING_FACT_ADDED, ChangeEventType.SUPPORTING_FACT_REMOVED}
_COUNTER_TYPES = {ChangeEventType.COUNTER_FACT_ADDED, ChangeEventType.COUNTER_FACT_REMOVED}
_TREND_TYPES = {ChangeEventType.TREND_CHANGED}
_GAP_TYPES = {ChangeEventType.GAP_ADDED, ChangeEventType.GAP_RESOLVED}
_RELEVANCE_TYPES = {ChangeEventType.MATCH_RELEVANCE_CHANGED}
_VALUE_TYPES = {ChangeEventType.VALUE_CHANGED}
_NEW_ISSUE_TYPES = {ChangeEventType.ISSUE_APPEARED}
_DISAPPEARED_ISSUE_TYPES = {ChangeEventType.ISSUE_DISAPPEARED}
_DECISION_LEVEL_TYPES = {ChangeEventType.DECISION_LEVEL_GAP_ADDED, ChangeEventType.DECISION_LEVEL_GAP_RESOLVED}


def _filter(events: list[ChangeEvent], types: set[ChangeEventType]) -> list[ChangeEvent]:
    return [event for event in events if event.type in types]


def build_challenge_result(db: Session, stability: DecisionStabilityResult) -> DecisionChallengeResult:
    """Reorganizes an already-computed Phase 4C `DecisionStabilityResult`
    into category buckets for frontend rendering. Computes nothing new —
    every event here was already produced by
    `app.services.decision_stability`. The only DB work is a batch
    ProductIssue lookup for display metadata (title/category/
    affected_feature/status), the same current-state convenience already
    applied to DecisionIssueEvidenceRead."""
    all_events = list(stability.events)

    issue_ids = {issue_change.product_issue_id for issue_change in stability.issue_changes}
    issues_by_id: dict[uuid.UUID, ProductIssue] = {}
    if issue_ids:
        issues_by_id = {
            issue.id: issue for issue in db.execute(select(ProductIssue).where(ProductIssue.id.in_(issue_ids))).scalars()
        }

    issue_summaries = {
        str(issue_id): ChallengeIssueSummary(
            product_issue_id=issue_id,
            title=issue.title if issue else None,
            category=issue.category if issue else None,
            affected_feature=issue.affected_feature if issue else None,
            status=issue.status.value if issue else None,
        )
        for issue_id, issue in issues_by_id.items()
    }
    # Cover issues referenced only via ISSUE_DISAPPEARED (may already be
    # captured above via issue_changes, but resolved defensively in case a
    # future Phase 4C revision ever surfaces an issue only through events).
    for event in all_events:
        if event.product_issue_id is not None and str(event.product_issue_id) not in issue_summaries:
            issue = issues_by_id.get(event.product_issue_id) or db.get(ProductIssue, event.product_issue_id)
            issue_summaries[str(event.product_issue_id)] = ChallengeIssueSummary(
                product_issue_id=event.product_issue_id,
                title=issue.title if issue else None,
                category=issue.category if issue else None,
                affected_feature=issue.affected_feature if issue else None,
                status=issue.status.value if issue else None,
            )

    return DecisionChallengeResult(
        stability_version=stability.stability_version,
        status=stability.status,
        evaluations_available=stability.evaluations_available,
        previous_evaluation_id=stability.previous_evaluation_id,
        current_evaluation_id=stability.current_evaluation_id,
        previous_reference_at=stability.previous_reference_at,
        current_reference_at=stability.current_reference_at,
        previous_evaluation_version=stability.previous_evaluation_version,
        current_evaluation_version=stability.current_evaluation_version,
        evaluation_version_mismatch=stability.evaluation_version_mismatch,
        reference_at_tie=stability.reference_at_tie,
        supporting_changes=_filter(all_events, _SUPPORTING_TYPES),
        counter_changes=_filter(all_events, _COUNTER_TYPES),
        trend_changes=_filter(all_events, _TREND_TYPES),
        gap_changes=_filter(all_events, _GAP_TYPES),
        relevance_changes=_filter(all_events, _RELEVANCE_TYPES),
        value_changes=_filter(all_events, _VALUE_TYPES),
        new_issues=_filter(all_events, _NEW_ISSUE_TYPES),
        disappeared_issues=_filter(all_events, _DISAPPEARED_ISSUE_TYPES),
        decision_level_changes=_filter(all_events, _DECISION_LEVEL_TYPES),
        issues=[
            IssueChallengeSummary(product_issue_id=ic.product_issue_id, status=ic.status, events=ic.events)
            for ic in stability.issue_changes
        ],
        issue_summaries=issue_summaries,
        all_events=all_events,
    )

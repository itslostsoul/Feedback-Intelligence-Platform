from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, SignalsMissingError
from app.db.database import get_db
from app.db.models.enums import FeedbackCategory, ProductIssueStatus
from app.schemas.issue import (
    IssueDiscoveryResponse,
    ProductIssueDetail,
    ProductIssueListResponse,
    ProductIssueRead,
)
from app.schemas.issue_signal import IssueSignalsBulkRecalculateResponse, ProductIssueSignalRead
from app.services.issue_discovery import (
    CLUSTERING_ALGORITHM_VERSION,
    count_feedback_for_issues,
    get_feedback_ids_for_issue,
    get_issue,
    list_issues,
    run_issue_discovery,
)
from app.services.issue_signals import (
    SIGNAL_ALGORITHM_VERSION,
    calculate_all_issue_signals,
    calculate_issue_signals,
    get_signals,
)

router = APIRouter(prefix="/issues", tags=["issues"])


@router.post("/discover", response_model=IssueDiscoveryResponse)
def discover_issues_endpoint(db: Session = Depends(get_db)) -> IssueDiscoveryResponse:
    """Explicitly triggered — never runs implicitly on a GET. Clusters
    whatever is currently in `feedback_relationship`; requires
    /semantic-analysis to have already been run for the relevant feedback."""
    result = run_issue_discovery(db)
    return IssueDiscoveryResponse(
        clustering_version=CLUSTERING_ALGORITHM_VERSION,
        components_considered=result.components_considered,
        issues_created=result.issues_created,
        issues_updated=result.issues_updated,
        feedback_linked=result.feedback_linked,
        feedback_left_unclustered=result.feedback_left_unclustered,
    )


@router.post("/signals/recalculate", response_model=IssueSignalsBulkRecalculateResponse)
def recalculate_all_issue_signals_endpoint(db: Session = Depends(get_db)) -> IssueSignalsBulkRecalculateResponse:
    """Recalculates Phase 3C signals for every ProductIssue. Deterministic,
    LLM-free aggregation — safe to call repeatedly (each issue's snapshot is
    overwritten, never duplicated)."""
    snapshots = calculate_all_issue_signals(db)
    return IssueSignalsBulkRecalculateResponse(
        signal_version=SIGNAL_ALGORITHM_VERSION, issues_processed=len(snapshots)
    )


@router.get("", response_model=ProductIssueListResponse)
def list_issues_endpoint(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: ProductIssueStatus | None = Query(default=None),
    category: FeedbackCategory | None = Query(default=None),
    db: Session = Depends(get_db),
) -> ProductIssueListResponse:
    issues, total = list_issues(db, limit=limit, offset=offset, status=status, category=category)
    counts = count_feedback_for_issues(db, [issue.id for issue in issues])

    items = [
        ProductIssueRead(
            id=issue.id,
            canonical_feedback_id=issue.canonical_feedback_id,
            title=issue.title,
            category=issue.category,
            affected_feature=issue.affected_feature,
            status=issue.status,
            clustering_version=issue.clustering_version,
            feedback_count=counts.get(issue.id, 0),
            created_at=issue.created_at,
            updated_at=issue.updated_at,
        )
        for issue in issues
    ]
    return ProductIssueListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{issue_id}", response_model=ProductIssueDetail)
def get_issue_endpoint(issue_id: UUID, db: Session = Depends(get_db)) -> ProductIssueDetail:
    issue = get_issue(db, issue_id)
    if issue is None:
        raise NotFoundError(f"Product issue {issue_id} not found.")

    feedback_ids = get_feedback_ids_for_issue(db, issue_id)

    return ProductIssueDetail(
        id=issue.id,
        canonical_feedback_id=issue.canonical_feedback_id,
        title=issue.title,
        category=issue.category,
        affected_feature=issue.affected_feature,
        status=issue.status,
        clustering_version=issue.clustering_version,
        feedback_count=len(feedback_ids),
        created_at=issue.created_at,
        updated_at=issue.updated_at,
        feedback_ids=feedback_ids,
    )


@router.post("/{issue_id}/signals/recalculate", response_model=ProductIssueSignalRead)
def recalculate_issue_signals_endpoint(issue_id: UUID, db: Session = Depends(get_db)) -> ProductIssueSignalRead:
    """Recalculates Phase 3C signals for one ProductIssue from its current
    feedback membership. Deterministic and LLM-free; safe to call
    repeatedly (overwrites the issue's existing snapshot, never duplicates)."""
    issue = get_issue(db, issue_id)
    if issue is None:
        raise NotFoundError(f"Product issue {issue_id} not found.")

    snapshot = calculate_issue_signals(db, issue)
    return ProductIssueSignalRead.model_validate(snapshot)


@router.get("/{issue_id}/signals", response_model=ProductIssueSignalRead)
def get_issue_signals_endpoint(issue_id: UUID, db: Session = Depends(get_db)) -> ProductIssueSignalRead:
    issue = get_issue(db, issue_id)
    if issue is None:
        raise NotFoundError(f"Product issue {issue_id} not found.")

    snapshot = get_signals(db, issue_id)
    if snapshot is None:
        raise SignalsMissingError(
            f"Signals for product issue {issue_id} have not been calculated yet. "
            "Call POST /signals/recalculate first."
        )
    return ProductIssueSignalRead.model_validate(snapshot)

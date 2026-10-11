from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_llm_provider
from app.core.errors import NotFoundError
from app.db.database import get_db
from app.db.models.decision import Decision
from app.db.models.decision_evaluation import DecisionEvaluation
from app.db.models.product_issue import ProductIssue
from app.schemas.decision import (
    DecisionCreate,
    DecisionEvaluationListResponse,
    DecisionEvaluationRead,
    DecisionEvaluationSummary,
    DecisionIssueEvidenceRead,
    DecisionListResponse,
    DecisionRead,
)
from app.schemas.decision_brief import DecisionBriefDetail, DecisionBriefListResponse, DecisionBriefSummary
from app.schemas.decision_challenge import DecisionChallengeResult
from app.schemas.decision_stability import DecisionStabilityResult
from app.services.decision_brief import (
    generate_brief,
    get_brief,
    get_latest_completed_brief,
    list_briefs,
    resolve_citations,
)
from app.services.decision_challenge import build_challenge_result
from app.services.decision_evidence import (
    evaluate_decision,
    get_evaluation,
    get_evidence_for_evaluation,
    get_latest_evaluation,
    list_evaluations,
)
from app.services.decision_stability import compare_evaluations, compare_latest_two
from app.services.llm.base import LLMProvider

router = APIRouter(prefix="/decisions", tags=["decisions"])


@router.post("", response_model=DecisionRead)
def create_decision(payload: DecisionCreate, response: Response, db: Session = Depends(get_db)) -> Decision:
    decision = Decision(
        query_text=payload.query_text,
        keywords=payload.keywords,
        category=payload.category,
        affected_feature_hint=payload.affected_feature_hint,
    )
    db.add(decision)
    db.commit()
    db.refresh(decision)
    response.status_code = 201
    return decision


@router.get("", response_model=DecisionListResponse)
def list_decisions(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> DecisionListResponse:
    total = db.execute(select(func.count()).select_from(Decision)).scalar_one()
    stmt = select(Decision).order_by(Decision.created_at.desc(), Decision.id.desc()).limit(limit).offset(offset)
    items = db.execute(stmt).scalars().all()
    return DecisionListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{decision_id}", response_model=DecisionRead)
def get_decision(decision_id: UUID, db: Session = Depends(get_db)) -> Decision:
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise NotFoundError(f"Decision {decision_id} not found.")
    return decision


def _enriched_evidence_read(row, issue: ProductIssue | None) -> DecisionIssueEvidenceRead:
    """Wraps DecisionIssueEvidenceRead.model_validate with a live
    ProductIssue join for display fields only — see that schema's
    docstring for why these are current-state, not historically pinned."""
    base = DecisionIssueEvidenceRead.model_validate(row)
    return base.model_copy(
        update={
            "issue_title": issue.title if issue else None,
            "issue_category": issue.category if issue else None,
            "issue_affected_feature": issue.affected_feature if issue else None,
            "issue_status": issue.status if issue else None,
        }
    )


def _to_evaluation_read(db: Session, evaluation: DecisionEvaluation) -> DecisionEvaluationRead:
    evidence_rows = get_evidence_for_evaluation(db, evaluation.id)
    issue_ids = {row.product_issue_id for row in evidence_rows}
    issues_by_id = (
        {issue.id: issue for issue in db.execute(select(ProductIssue).where(ProductIssue.id.in_(issue_ids))).scalars()}
        if issue_ids
        else {}
    )
    return DecisionEvaluationRead(
        id=evaluation.id,
        decision_id=evaluation.decision_id,
        evaluation_version=evaluation.evaluation_version,
        reference_at=evaluation.reference_at,
        created_at=evaluation.created_at,
        decision_level_gaps=evaluation.decision_level_gaps,
        issues=[_enriched_evidence_read(row, issues_by_id.get(row.product_issue_id)) for row in evidence_rows],
    )


@router.post("/{decision_id}/evaluate", response_model=DecisionEvaluationRead)
def evaluate_decision_endpoint(decision_id: UUID, db: Session = Depends(get_db)) -> DecisionEvaluationRead:
    """Deterministic matching + evidence classification only — no LLM call,
    no signal recalculation (reads whatever ProductIssueSignalSnapshot rows
    currently exist). Always creates a NEW DecisionEvaluation; previous
    evaluations for this decision are never modified (append-only, see
    DecisionEvaluation's docstring)."""
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise NotFoundError(f"Decision {decision_id} not found.")

    evaluation = evaluate_decision(db, decision)
    return _to_evaluation_read(db, evaluation)


@router.get("/{decision_id}/evaluations", response_model=DecisionEvaluationListResponse)
def list_decision_evaluations_endpoint(
    decision_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> DecisionEvaluationListResponse:
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise NotFoundError(f"Decision {decision_id} not found.")

    evaluations, total = list_evaluations(db, decision_id, limit=limit, offset=offset)
    items = []
    for evaluation in evaluations:
        matched_issue_count = len(get_evidence_for_evaluation(db, evaluation.id))
        items.append(
            DecisionEvaluationSummary(
                id=evaluation.id,
                decision_id=evaluation.decision_id,
                evaluation_version=evaluation.evaluation_version,
                reference_at=evaluation.reference_at,
                created_at=evaluation.created_at,
                decision_level_gaps=evaluation.decision_level_gaps,
                matched_issue_count=matched_issue_count,
            )
        )
    return DecisionEvaluationListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{decision_id}/evaluations/latest", response_model=DecisionEvaluationRead)
def get_latest_decision_evaluation_endpoint(decision_id: UUID, db: Session = Depends(get_db)) -> DecisionEvaluationRead:
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise NotFoundError(f"Decision {decision_id} not found.")

    evaluation = get_latest_evaluation(db, decision_id)
    if evaluation is None:
        raise NotFoundError(f"Decision {decision_id} has no evaluations yet. Call POST .../evaluate first.")
    return _to_evaluation_read(db, evaluation)


@router.get("/{decision_id}/evaluations/{evaluation_id}", response_model=DecisionEvaluationRead)
def get_decision_evaluation_endpoint(
    decision_id: UUID, evaluation_id: UUID, db: Session = Depends(get_db)
) -> DecisionEvaluationRead:
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise NotFoundError(f"Decision {decision_id} not found.")

    evaluation = get_evaluation(db, evaluation_id)
    if evaluation is None or evaluation.decision_id != decision_id:
        raise NotFoundError(f"Evaluation {evaluation_id} not found for decision {decision_id}.")

    return _to_evaluation_read(db, evaluation)


def _to_brief_detail(db: Session, brief, evaluation: DecisionEvaluation) -> DecisionBriefDetail:
    return DecisionBriefDetail(
        id=brief.id,
        decision_evaluation_id=brief.decision_evaluation_id,
        status=brief.status,
        evidence_sufficiency=brief.evidence_sufficiency,
        headline=brief.headline,
        supporting_narrative=brief.supporting_narrative,
        counter_narrative=brief.counter_narrative,
        gap_narrative=brief.gap_narrative,
        provider=brief.provider,
        model=brief.model,
        generation_version=brief.generation_version,
        error_code=brief.error_code,
        generated_at=brief.generated_at,
        created_at=brief.created_at,
        citations=resolve_citations(db, evaluation),
    )


@router.post("/{decision_id}/brief", response_model=DecisionBriefDetail)
def generate_decision_brief_endpoint(
    decision_id: UUID,
    db: Session = Depends(get_db),
    provider: LLMProvider = Depends(get_llm_provider),
) -> DecisionBriefDetail:
    """Grounded LLM synthesis over the decision's LATEST DecisionEvaluation
    (Phase 4B) — the LLM only ever sees already-persisted Phase 4A facts,
    never raw feedback, never a chance to compute a new aggregate. Always
    appends a new DecisionBrief (COMPLETED or FAILED); on failure the
    failed attempt is persisted before the exception propagates to the
    existing global error handler."""
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise NotFoundError(f"Decision {decision_id} not found.")

    evaluation = get_latest_evaluation(db, decision_id)
    if evaluation is None:
        raise NotFoundError(f"Decision {decision_id} has no evaluations yet. Call POST .../evaluate first.")

    brief = generate_brief(db, decision, evaluation, provider)
    return _to_brief_detail(db, brief, evaluation)


@router.get("/{decision_id}/brief", response_model=DecisionBriefDetail)
def get_latest_decision_brief_endpoint(decision_id: UUID, db: Session = Depends(get_db)) -> DecisionBriefDetail:
    """Latest COMPLETED brief across all evaluations for this decision.
    FAILED attempts are never returned here — see .../brief/history for
    the full attempt log."""
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise NotFoundError(f"Decision {decision_id} not found.")

    brief = get_latest_completed_brief(db, decision_id)
    if brief is None:
        raise NotFoundError(f"Decision {decision_id} has no completed brief yet. Call POST .../brief first.")

    evaluation = get_evaluation(db, brief.decision_evaluation_id)
    return _to_brief_detail(db, brief, evaluation)


@router.get("/{decision_id}/brief/history", response_model=DecisionBriefListResponse)
def list_decision_brief_history_endpoint(
    decision_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> DecisionBriefListResponse:
    """All generation attempts — COMPLETED and FAILED — newest first."""
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise NotFoundError(f"Decision {decision_id} not found.")

    briefs, total = list_briefs(db, decision_id, limit=limit, offset=offset)
    items = [DecisionBriefSummary.model_validate(brief) for brief in briefs]
    return DecisionBriefListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{decision_id}/brief/{brief_id}", response_model=DecisionBriefDetail)
def get_decision_brief_endpoint(decision_id: UUID, brief_id: UUID, db: Session = Depends(get_db)) -> DecisionBriefDetail:
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise NotFoundError(f"Decision {decision_id} not found.")

    brief = get_brief(db, brief_id)
    if brief is None:
        raise NotFoundError(f"Brief {brief_id} not found for decision {decision_id}.")

    evaluation = get_evaluation(db, brief.decision_evaluation_id)
    if evaluation is None or evaluation.decision_id != decision_id:
        raise NotFoundError(f"Brief {brief_id} not found for decision {decision_id}.")

    return _to_brief_detail(db, brief, evaluation)


@router.get("/{decision_id}/stability", response_model=DecisionStabilityResult)
def get_decision_stability_endpoint(decision_id: UUID, db: Session = Depends(get_db)) -> DecisionStabilityResult:
    """Deterministic comparison of the decision's two most recent
    evaluations (by reference_at — never created_at). No LLM call, no
    signal recalculation: reads only persisted DecisionEvaluation/
    DecisionIssueEvidence rows (Phase 4A), never the current
    ProductIssueSignalSnapshot. Never errors for "not enough history yet" —
    returns `status: "insufficient_history"` (200) if the decision has
    fewer than 2 evaluations, since a second evaluation simply not having
    happened yet is a normal state, not a mistake the caller made."""
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise NotFoundError(f"Decision {decision_id} not found.")

    return compare_latest_two(db, decision_id)


@router.get(
    "/{decision_id}/evaluations/{from_evaluation_id}/compare/{to_evaluation_id}",
    response_model=DecisionStabilityResult,
)
def compare_decision_evaluations_endpoint(
    decision_id: UUID, from_evaluation_id: UUID, to_evaluation_id: UUID, db: Session = Depends(get_db)
) -> DecisionStabilityResult:
    """Explicit comparison between any two of the decision's evaluations —
    not limited to adjacent ones. `from_evaluation_id` is treated as
    "previous" and `to_evaluation_id` as "current" exactly as the caller
    ordered them (unlike the default .../stability endpoint, which always
    orders by reference_at); both evaluations' reference_at are included in
    the response so a caller comparing "backwards in time" can see that
    plainly."""
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise NotFoundError(f"Decision {decision_id} not found.")

    previous = get_evaluation(db, from_evaluation_id)
    if previous is None or previous.decision_id != decision_id:
        raise NotFoundError(f"Evaluation {from_evaluation_id} not found for decision {decision_id}.")

    current = get_evaluation(db, to_evaluation_id)
    if current is None or current.decision_id != decision_id:
        raise NotFoundError(f"Evaluation {to_evaluation_id} not found for decision {decision_id}.")

    return compare_evaluations(db, previous, current)


@router.get("/{decision_id}/challenge", response_model=DecisionChallengeResult)
def get_decision_challenge_endpoint(decision_id: UUID, db: Session = Depends(get_db)) -> DecisionChallengeResult:
    """"Challenge My Decision" — NOT a recommendation engine: returns no
    ranking, score, or verdict. A read-only, categorized regrouping of the
    decision's latest-vs-second-latest Phase 4C comparison (the same
    `compare_latest_two` used by GET .../stability) into frontend-friendly
    buckets (supporting/counter/trend/gap/relevance/value changes, new/
    disappeared issues), with issue titles resolved for display. Computes
    nothing new — every event is produced by
    `app.services.decision_stability`; this only reorganizes and annotates
    it. Same `insufficient_history` behavior as .../stability: 200, never
    an error, when fewer than 2 evaluations exist."""
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise NotFoundError(f"Decision {decision_id} not found.")

    stability = compare_latest_two(db, decision_id)
    return build_challenge_result(db, stability)

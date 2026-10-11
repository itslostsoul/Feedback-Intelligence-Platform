import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import LLMOutputValidationError, LLMProviderError, LLMUngroundedOutputError
from app.db.models.decision import Decision
from app.db.models.decision_brief import DecisionBrief
from app.db.models.decision_evaluation import DecisionEvaluation
from app.db.models.decision_issue_evidence import DecisionIssueEvidence
from app.db.models.enums import EvidenceSufficiency, GenerationStatus
from app.db.models.product_issue import ProductIssue
from app.schemas.decision_brief import (
    DecisionBriefLLMOutput,
    DecisionSynthesisInput,
    ResolvedCitation,
    SynthesisFact,
    SynthesisIssue,
)
from app.services.decision_evidence import get_evidence_for_evaluation
from app.services.llm.base import LLMProvider

logger = logging.getLogger(__name__)

# Bump whenever the prompt text, the DecisionSynthesisInput-building logic,
# or the output-validation/grounding rules below change — this one constant
# deliberately bundles all three (they always ship and change together in
# this phase), same simplification Phase 4A already made for
# DECISION_EVALUATION_VERSION. Stamped on every DecisionBrief, successful or
# failed, so a persisted row always identifies exactly which logic produced
# (or attempted to produce) it.
DECISION_BRIEF_SYNTHESIS_VERSION = "brief-synthesis-v1"


def _citation_id(product_issue_id: uuid.UUID | None, code: str) -> str:
    return f"{product_issue_id}:{code}" if product_issue_id is not None else f"decision:{code}"


def _facts_from_raw(
    raw_facts: list[dict], product_issue_id: uuid.UUID | None, category: str
) -> list[SynthesisFact]:
    return [
        SynthesisFact(
            citation_id=_citation_id(product_issue_id, raw["code"]),
            code=raw["code"],
            label=raw["label"],
            value=raw["value"],
            threshold=raw["threshold"],
            source_signal_path=raw["source_signal_path"],
            category=category,
        )
        for raw in raw_facts
    ]


def compute_evidence_sufficiency(
    evidence_rows: list[DecisionIssueEvidence],
) -> EvidenceSufficiency:
    """Backend-authoritative, computed purely from persisted Phase 4A rows
    BEFORE any LLM call — never influenced by, and never waits on, what the
    LLM writes. See EvidenceSufficiency's docstring for the exact semantics
    of each value.
    """
    if not evidence_rows:
        return EvidenceSufficiency.NO_MATCHES
    if any(row.supporting or row.counter for row in evidence_rows):
        return EvidenceSufficiency.SUFFICIENT
    return EvidenceSufficiency.INSUFFICIENT


def build_synthesis_input(
    db: Session,
    decision: Decision,
    evaluation: DecisionEvaluation,
    *,
    evidence_rows: list[DecisionIssueEvidence] | None = None,
) -> DecisionSynthesisInput:
    """Builds the complete, deterministic LLM input from persisted Phase 4A
    rows only (plus a read-only join to ProductIssue for title/category/
    affected_feature — contextual labels, never evidence; see
    SynthesisIssue's docstring). No LLM call, no new signal computation, no
    raw feedback access. Same evaluation always produces a byte-identical
    DecisionSynthesisInput.
    """
    if evidence_rows is None:
        evidence_rows = get_evidence_for_evaluation(db, evaluation.id)

    issue_ids = [row.product_issue_id for row in evidence_rows]
    issues_by_id: dict[uuid.UUID, ProductIssue] = {}
    if issue_ids:
        issues_by_id = {
            issue.id: issue
            for issue in db.execute(select(ProductIssue).where(ProductIssue.id.in_(issue_ids))).scalars()
        }

    synthesis_issues = []
    for row in evidence_rows:
        product_issue = issues_by_id.get(row.product_issue_id)
        synthesis_issues.append(
            SynthesisIssue(
                product_issue_id=row.product_issue_id,
                title=product_issue.title if product_issue else "",
                category=product_issue.category if product_issue else None,
                affected_feature=product_issue.affected_feature if product_issue else None,
                match_method=row.match_method,
                match_score=row.match_score,
                matched_terms=row.matched_terms,
                volume_summary=row.volume_summary,
                supporting=_facts_from_raw(row.supporting, row.product_issue_id, "supporting"),
                counter=_facts_from_raw(row.counter, row.product_issue_id, "counter"),
                gaps=_facts_from_raw(row.gaps, row.product_issue_id, "gap"),
            )
        )

    return DecisionSynthesisInput(
        decision_id=decision.id,
        decision_evaluation_id=evaluation.id,
        query_text=decision.query_text,
        keywords=decision.keywords,
        category=decision.category,
        affected_feature_hint=decision.affected_feature_hint,
        issues=synthesis_issues,
        decision_level_gaps=_facts_from_raw(evaluation.decision_level_gaps, None, "gap"),
    )


def _first_unknown_citation(output: DecisionBriefLLMOutput, registry: dict[str, SynthesisFact]) -> str | None:
    for claim in (*output.supporting_narrative, *output.counter_narrative, *output.gap_narrative):
        for citation_id in claim.citation_ids:
            if citation_id not in registry:
                return citation_id
    return None


def generate_brief(
    db: Session,
    decision: Decision,
    evaluation: DecisionEvaluation,
    provider: LLMProvider,
    settings: Settings | None = None,
) -> DecisionBrief:
    """Runs one grounded brief-generation attempt against `evaluation` and
    always appends a new DecisionBrief row (COMPLETED or FAILED — see
    DecisionBrief's docstring for why this is append-only). No signal
    recalculation, no new evidence computation: `evidence_sufficiency` is
    computed purely from already-persisted Phase 4A rows before the provider
    is ever called.

    On any failure (provider error, malformed/invalid output, or an
    ungrounded citation), the failure is persisted with `error_code` set and
    no narrative content, and the same exception is re-raised for the
    caller (API layer) to let propagate to the existing global exception
    handler — no new response/error architecture.
    """
    settings = settings or get_settings()
    evidence_rows = get_evidence_for_evaluation(db, evaluation.id)
    sufficiency = compute_evidence_sufficiency(evidence_rows)
    synthesis_input = build_synthesis_input(db, decision, evaluation, evidence_rows=evidence_rows)
    registry = synthesis_input.citation_registry()
    generated_at = datetime.now(timezone.utc)

    try:
        output = provider.synthesize_decision_brief(synthesis_input)
    except (LLMProviderError, LLMOutputValidationError) as exc:
        logger.warning(
            "decision_brief_generation_failed decision_id=%s evaluation_id=%s error=%s",
            decision.id,
            evaluation.id,
            type(exc).__name__,
        )
        db.add(
            DecisionBrief(
                decision_evaluation_id=evaluation.id,
                status=GenerationStatus.FAILED,
                evidence_sufficiency=sufficiency,
                provider=settings.LLM_PROVIDER,
                model=settings.LLM_MODEL,
                generation_version=DECISION_BRIEF_SYNTHESIS_VERSION,
                error_code=exc.error_code,
                generated_at=generated_at,
            )
        )
        db.commit()
        raise

    unknown_citation = _first_unknown_citation(output, registry)
    if unknown_citation is not None:
        logger.warning(
            "decision_brief_ungrounded_citation decision_id=%s evaluation_id=%s citation_id=%s",
            decision.id,
            evaluation.id,
            unknown_citation,
        )
        db.add(
            DecisionBrief(
                decision_evaluation_id=evaluation.id,
                status=GenerationStatus.FAILED,
                evidence_sufficiency=sufficiency,
                provider=settings.LLM_PROVIDER,
                model=settings.LLM_MODEL,
                generation_version=DECISION_BRIEF_SYNTHESIS_VERSION,
                error_code=LLMUngroundedOutputError.error_code,
                generated_at=generated_at,
            )
        )
        db.commit()
        raise LLMUngroundedOutputError(f"The LLM provider cited an unknown citation_id: {unknown_citation!r}.")

    brief = DecisionBrief(
        decision_evaluation_id=evaluation.id,
        status=GenerationStatus.COMPLETED,
        evidence_sufficiency=sufficiency,
        headline=output.headline,
        supporting_narrative=[claim.model_dump() for claim in output.supporting_narrative],
        counter_narrative=[claim.model_dump() for claim in output.counter_narrative],
        gap_narrative=[claim.model_dump() for claim in output.gap_narrative],
        provider=settings.LLM_PROVIDER,
        model=settings.LLM_MODEL,
        generation_version=DECISION_BRIEF_SYNTHESIS_VERSION,
        error_code=None,
        generated_at=generated_at,
    )
    db.add(brief)
    db.commit()
    db.refresh(brief)
    logger.info(
        "decision_brief_generated decision_id=%s evaluation_id=%s brief_id=%s sufficiency=%s",
        decision.id,
        evaluation.id,
        brief.id,
        sufficiency.value,
    )
    return brief


def get_brief(db: Session, brief_id: uuid.UUID) -> DecisionBrief | None:
    return db.get(DecisionBrief, brief_id)


def get_latest_completed_brief(db: Session, decision_id: uuid.UUID) -> DecisionBrief | None:
    """Ordered by `generated_at` (Python-computed per generation), NOT
    `created_at` (a Postgres `now()` server default that can tie across
    rows committed in the same transaction) — same fix already applied to
    DecisionEvaluation ordering in Phase 4A."""
    stmt = (
        select(DecisionBrief)
        .join(DecisionEvaluation, DecisionBrief.decision_evaluation_id == DecisionEvaluation.id)
        .where(DecisionEvaluation.decision_id == decision_id, DecisionBrief.status == GenerationStatus.COMPLETED)
        .order_by(DecisionBrief.generated_at.desc(), DecisionBrief.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def list_briefs(db: Session, decision_id: uuid.UUID, *, limit: int, offset: int) -> tuple[list[DecisionBrief], int]:
    """Includes both COMPLETED and FAILED attempts, newest first."""
    base = (
        select(DecisionBrief)
        .join(DecisionEvaluation, DecisionBrief.decision_evaluation_id == DecisionEvaluation.id)
        .where(DecisionEvaluation.decision_id == decision_id)
    )
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()

    stmt = base.order_by(DecisionBrief.generated_at.desc(), DecisionBrief.id.desc()).limit(limit).offset(offset)
    items = list(db.execute(stmt).scalars())
    return items, total


def resolve_citations(db: Session, evaluation: DecisionEvaluation) -> dict[str, ResolvedCitation]:
    """Reconstructs the full citation → provenance mapping live from the
    immutable Phase 4A evidence tied to `evaluation` — never from a copy
    stored on DecisionBrief (none is kept; see DecisionBrief's docstring)."""
    evidence_rows = get_evidence_for_evaluation(db, evaluation.id)
    issue_ids = [row.product_issue_id for row in evidence_rows]
    issues_by_id: dict[uuid.UUID, ProductIssue] = {}
    if issue_ids:
        issues_by_id = {
            issue.id: issue
            for issue in db.execute(select(ProductIssue).where(ProductIssue.id.in_(issue_ids))).scalars()
        }

    citations: dict[str, ResolvedCitation] = {}
    for row in evidence_rows:
        issue = issues_by_id.get(row.product_issue_id)
        for raw_facts, category in ((row.supporting, "supporting"), (row.counter, "counter"), (row.gaps, "gap")):
            for raw in raw_facts:
                citation_id = _citation_id(row.product_issue_id, raw["code"])
                citations[citation_id] = ResolvedCitation(
                    citation_id=citation_id,
                    product_issue_id=row.product_issue_id,
                    issue_title=issue.title if issue else None,
                    code=raw["code"],
                    label=raw["label"],
                    value=raw["value"],
                    threshold=raw["threshold"],
                    source_signal_path=raw["source_signal_path"],
                    category=category,
                )

    for raw in evaluation.decision_level_gaps:
        citation_id = _citation_id(None, raw["code"])
        citations[citation_id] = ResolvedCitation(
            citation_id=citation_id,
            product_issue_id=None,
            issue_title=None,
            code=raw["code"],
            label=raw["label"],
            value=raw["value"],
            threshold=raw["threshold"],
            source_signal_path=raw["source_signal_path"],
            category="gap",
        )

    return citations

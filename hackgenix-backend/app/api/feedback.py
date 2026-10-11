from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_embedding_provider, get_llm_provider
from app.core.errors import AnalysisMissingError, NotFoundError
from app.db.database import get_db
from app.db.models.enums import ProcessingStatus, SourceType
from app.db.models.feedback import Feedback
from app.schemas.analysis import AnalyzeFeedbackResponse, FeedbackAnalysisRead
from app.schemas.embedding import EmbedFeedbackResponse, FeedbackEmbeddingRead
from app.schemas.feedback import (
    BulkIngestResponse,
    FeedbackCreate,
    FeedbackListResponse,
    FeedbackRead,
)
from app.schemas.semantic import FeedbackRelationshipRead, SemanticAnalysisResponse, SimilarFeedbackResponse
from app.services.embedding.base import EmbeddingProvider
from app.services.feedback_analysis import analyze_feedback, get_analysis_for_feedback
from app.services.feedback_embedding import embed_feedback
from app.services.feedback_ingestion import (
    create_or_get_feedback,
    parse_and_ingest_csv,
    parse_and_ingest_json,
)
from app.services.llm.base import LLMProvider
from app.services.semantic_engine import (
    DEFAULT_CANDIDATE_LIMIT,
    DEFAULT_SIMILARITY_FLOOR,
    MAX_CANDIDATE_LIMIT,
    SEMANTIC_ANALYSIS_ALGORITHM_VERSION,
    find_similar_feedback,
    run_semantic_analysis,
)

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post("", response_model=FeedbackRead)
def ingest_feedback(
    payload: FeedbackCreate, response: Response, db: Session = Depends(get_db)
) -> Feedback:
    """Direct REST ingestion — the integration point for a company's own
    application/backend to push feedback in as it happens. Deduplicates on
    (source, external_id): a repeat of an already-seen pair returns the
    existing row with `200 OK` instead of creating a second one; a new pair
    returns `201 Created`. Goes through the exact same common ingestion
    service (`create_or_get_feedback`) as CSV bulk import, webhook
    ingestion, and scheduled sync."""
    feedback, was_duplicate = create_or_get_feedback(db, payload)
    response.status_code = 200 if was_duplicate else 201
    return feedback


@router.get("/{feedback_id}", response_model=FeedbackRead)
def get_feedback(feedback_id: UUID, db: Session = Depends(get_db)) -> Feedback:
    feedback = db.get(Feedback, feedback_id)
    if feedback is None:
        raise NotFoundError(f"Feedback {feedback_id} not found.")
    return feedback


@router.get("", response_model=FeedbackListResponse)
def list_feedback(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    source: str | None = Query(default=None),
    source_type: SourceType | None = Query(default=None),
    status: ProcessingStatus | None = Query(default=None),
    db: Session = Depends(get_db),
) -> FeedbackListResponse:
    filters = []
    if source:
        filters.append(Feedback.source == source)
    if source_type:
        filters.append(Feedback.source_type == source_type)
    if status:
        filters.append(Feedback.processing_status == status)

    count_stmt = select(func.count()).select_from(Feedback)
    stmt = select(Feedback)
    for condition in filters:
        count_stmt = count_stmt.where(condition)
        stmt = stmt.where(condition)

    total = db.execute(count_stmt).scalar_one()
    stmt = stmt.order_by(Feedback.ingested_at.desc(), Feedback.id.desc()).limit(limit).offset(offset)
    items = db.execute(stmt).scalars().all()

    return FeedbackListResponse(items=items, total=total, limit=limit, offset=offset)


@router.post("/bulk", response_model=BulkIngestResponse)
def bulk_ingest_feedback(
    file: UploadFile = File(...), db: Session = Depends(get_db)
) -> BulkIngestResponse:
    """Bulk CSV ingestion — for a company exporting thousands of existing
    feedback records at once instead of typing them in one at a time.
    Required columns: `source`, `raw_text`; optional: `external_id`,
    `source_type`, `customer_id`, `customer_tier`, `language`,
    `created_at`. A structurally broken file (empty, wrong encoding,
    missing required columns, too many rows) is rejected outright; an
    individual bad row is recorded in `errors` and skipped without
    aborting the rest of the import. Deduplicates on (source, external_id)
    via the same common ingestion service used by every other channel —
    never creates a duplicate row for a pair already seen, whether earlier
    in this same file or in a previous import."""
    raw_bytes = file.file.read()
    return parse_and_ingest_csv(db, raw_bytes)


@router.post("/bulk/json", response_model=BulkIngestResponse)
def bulk_ingest_json_feedback(
    file: UploadFile = File(...), db: Session = Depends(get_db)
) -> BulkIngestResponse:
    """Bulk JSON ingestion — the same "thousands of historical records at
    once" use case as CSV bulk import, for a company whose export format is
    JSON instead of CSV. Expects a file containing a top-level JSON array
    of objects shaped like the REST `POST /feedback` payload, e.g.:

    ```
    [{"external_id": "review_001", "source": "app_store",
      "source_type": "review", "raw_text": "..."}, ...]
    ```

    Same behavior as CSV bulk import in every other respect: a structurally
    broken file (empty, invalid JSON, a top-level object instead of an
    array, too many records) is rejected outright; an individual bad
    record is recorded in `errors` and skipped without aborting the rest.
    Deduplicates on (source, external_id) via the same common ingestion
    service used by every other channel (REST, CSV, webhook, scheduled
    sync) — never creates a duplicate row for a pair already seen,
    whether earlier in this same file or in a previous import. Only stores
    records (`RECEIVED` status); never runs analysis/embedding
    synchronously for the uploaded records."""
    raw_bytes = file.file.read()
    return parse_and_ingest_json(db, raw_bytes)


@router.post("/{feedback_id}/analyze", response_model=AnalyzeFeedbackResponse)
def analyze_feedback_endpoint(
    feedback_id: UUID,
    db: Session = Depends(get_db),
    provider: LLMProvider = Depends(get_llm_provider),
) -> AnalyzeFeedbackResponse:
    feedback = db.get(Feedback, feedback_id)
    if feedback is None:
        raise NotFoundError(f"Feedback {feedback_id} not found.")

    updated = analyze_feedback(db, feedback, provider)
    analysis = get_analysis_for_feedback(db, updated.id)

    return AnalyzeFeedbackResponse(
        feedback_id=updated.id,
        processing_status=updated.processing_status,
        analysis=FeedbackAnalysisRead.model_validate(analysis),
    )


@router.get("/{feedback_id}/analysis", response_model=FeedbackAnalysisRead)
def get_feedback_analysis_endpoint(feedback_id: UUID, db: Session = Depends(get_db)) -> FeedbackAnalysisRead:
    """Read-only: retrieves an already-computed analysis without
    triggering a new (LLM-calling) one. `404 not_found` if the feedback
    item doesn't exist; `404 analysis_missing` if it exists but hasn't been
    analyzed yet (call POST .../analyze first)."""
    feedback = db.get(Feedback, feedback_id)
    if feedback is None:
        raise NotFoundError(f"Feedback {feedback_id} not found.")

    analysis = get_analysis_for_feedback(db, feedback_id)
    if analysis is None:
        raise AnalysisMissingError(f"Feedback {feedback_id} has not been analyzed yet. Call POST .../analyze first.")

    return FeedbackAnalysisRead.model_validate(analysis)


@router.post("/{feedback_id}/embed", response_model=EmbedFeedbackResponse)
def embed_feedback_endpoint(
    feedback_id: UUID,
    db: Session = Depends(get_db),
    provider: EmbeddingProvider = Depends(get_embedding_provider),
) -> EmbedFeedbackResponse:
    feedback = db.get(Feedback, feedback_id)
    if feedback is None:
        raise NotFoundError(f"Feedback {feedback_id} not found.")

    embedding, was_generated = embed_feedback(db, feedback, provider)

    return EmbedFeedbackResponse(
        feedback_id=feedback.id,
        embedding=FeedbackEmbeddingRead.model_validate(embedding),
        was_generated=was_generated,
    )


@router.get("/{feedback_id}/similar", response_model=SimilarFeedbackResponse)
def similar_feedback_endpoint(
    feedback_id: UUID,
    limit: int = Query(default=DEFAULT_CANDIDATE_LIMIT, ge=1, le=MAX_CANDIDATE_LIMIT),
    threshold: float = Query(default=DEFAULT_SIMILARITY_FLOOR, ge=0.0, le=1.0),
    db: Session = Depends(get_db),
) -> SimilarFeedbackResponse:
    """Read-only preview: finds and classifies semantic neighbors without
    persisting anything. Use POST .../semantic-analysis to persist findings."""
    feedback = db.get(Feedback, feedback_id)
    if feedback is None:
        raise NotFoundError(f"Feedback {feedback_id} not found.")

    candidates = find_similar_feedback(db, feedback, limit=limit, similarity_threshold=threshold)

    return SimilarFeedbackResponse(
        feedback_id=feedback.id,
        algorithm_version=SEMANTIC_ANALYSIS_ALGORITHM_VERSION,
        candidates=candidates,
    )


@router.post("/{feedback_id}/semantic-analysis", response_model=SemanticAnalysisResponse)
def semantic_analysis_endpoint(
    feedback_id: UUID,
    limit: int = Query(default=DEFAULT_CANDIDATE_LIMIT, ge=1, le=MAX_CANDIDATE_LIMIT),
    threshold: float = Query(default=DEFAULT_SIMILARITY_FLOOR, ge=0.0, le=1.0),
    db: Session = Depends(get_db),
) -> SemanticAnalysisResponse:
    """Finds semantic neighbors and persists a FeedbackRelationship row for
    every DUPLICATE/RELATED candidate found (DISTINCT candidates are
    returned in the count but not persisted)."""
    feedback = db.get(Feedback, feedback_id)
    if feedback is None:
        raise NotFoundError(f"Feedback {feedback_id} not found.")

    candidates, relationships = run_semantic_analysis(
        db, feedback, limit=limit, similarity_threshold=threshold
    )

    return SemanticAnalysisResponse(
        feedback_id=feedback.id,
        algorithm_version=SEMANTIC_ANALYSIS_ALGORITHM_VERSION,
        candidates_considered=len(candidates),
        relationships=[FeedbackRelationshipRead.model_validate(r) for r in relationships],
    )

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import EmbeddingMissingError
from app.db.models.enums import RelationshipType
from app.db.models.feedback import Feedback
from app.db.models.feedback_analysis import FeedbackAnalysis
from app.db.models.feedback_embedding import FeedbackEmbedding
from app.db.models.feedback_relationship import FeedbackRelationship
from app.schemas.semantic import SimilarFeedbackItem
from app.services.feedback_embedding import get_embedding_for_feedback

logger = logging.getLogger(__name__)

# --- Candidate retrieval configuration ---------------------------------------
# Defaults, not hard limits — both are accepted as request parameters.
# DEFAULT_CANDIDATE_LIMIT: how many nearest neighbors pgvector returns at
# most. Kept small (hackathon/demo scale); revisit once feedback volume
# actually requires paging through neighbors.
DEFAULT_CANDIDATE_LIMIT = 10
MAX_CANDIDATE_LIMIT = 50

# DEFAULT_SIMILARITY_FLOOR: candidates below this cosine similarity are not
# retrieved at all (they're pushed to Postgres's WHERE clause, not filtered
# in Python) because they're clearly unrelated text — no point paying the
# classification step for them. This is deliberately *below*
# RELATED_SIMILARITY_THRESHOLD so classify_relationship() can still see (and
# label DISTINCT) borderline candidates instead of silently dropping them.
DEFAULT_SIMILARITY_FLOOR = 0.55

# --- Classification thresholds (see docs/architecture.md "Phase 3B-2") ------
# Chosen for gemini-embedding-2 cosine similarity on short product-feedback
# text: near-duplicate phrasing of the same complaint typically lands
# >= 0.93; genuinely different feedback about the same feature typically
# lands in the 0.75-0.93 band; below 0.75 the text is normally about
# something else entirely. These are deliberately conservative (favor
# DISTINCT/RELATED over DUPLICATE) since a false "duplicate" silently
# discards a distinct signal, which is worse for signal quality than an
# extra RELATED row a human can dismiss.
DUPLICATE_SIMILARITY_THRESHOLD = 0.93
RELATED_SIMILARITY_THRESHOLD = 0.75

# A severity gap this large between two otherwise-similar items is treated
# as evidence they're not really the same issue (e.g. a minor cosmetic
# complaint vs. a data-loss bug that happen to use similar words).
SEVERITY_DOWNGRADE_GAP = 5

# Bump this whenever DUPLICATE_SIMILARITY_THRESHOLD, RELATED_SIMILARITY_THRESHOLD,
# or the structured-signal rules in classify_relationship() change, so a
# persisted FeedbackRelationship row's algorithm_version always identifies
# exactly which thresholds/logic produced it (requirement: reproducibility).
SEMANTIC_ANALYSIS_ALGORITHM_VERSION = "semantic-v1"


def _downgrade(relationship_type: RelationshipType) -> RelationshipType:
    if relationship_type == RelationshipType.DUPLICATE:
        return RelationshipType.RELATED
    if relationship_type == RelationshipType.RELATED:
        return RelationshipType.DISTINCT
    return relationship_type


def classify_relationship(
    similarity: float,
    source_analysis: FeedbackAnalysis | None,
    candidate_analysis: FeedbackAnalysis | None,
) -> RelationshipType:
    """Deterministic first-stage classifier. No LLM call — pure rules over
    the cosine similarity plus (when available) structured FeedbackAnalysis
    fields.

    Structured signals only ever *downgrade* a similarity-based
    classification, never upgrade it: analysis is optional, and its absence
    must never manufacture a stronger claim than the embeddings support on
    their own.
    """
    if similarity >= DUPLICATE_SIMILARITY_THRESHOLD:
        relationship_type = RelationshipType.DUPLICATE
    elif similarity >= RELATED_SIMILARITY_THRESHOLD:
        relationship_type = RelationshipType.RELATED
    else:
        return RelationshipType.DISTINCT

    if source_analysis is None or candidate_analysis is None:
        return relationship_type

    if source_analysis.category != candidate_analysis.category:
        # Different categories (e.g. Bug vs. Feature Request) contradict
        # "same underlying problem" even at high text similarity.
        return _downgrade(relationship_type)

    if (
        source_analysis.affected_feature.strip().lower()
        != candidate_analysis.affected_feature.strip().lower()
    ):
        # Same category, but a clearly different feature/module — only
        # a duplicate claim is strong enough to be worth downgrading for
        # this; "related" already tolerates some feature drift.
        if relationship_type == RelationshipType.DUPLICATE:
            relationship_type = RelationshipType.RELATED

    if abs(source_analysis.severity - candidate_analysis.severity) >= SEVERITY_DOWNGRADE_GAP:
        relationship_type = _downgrade(relationship_type)

    return relationship_type


def _load_analyses(db: Session, feedback_ids: list[uuid.UUID]) -> dict[uuid.UUID, FeedbackAnalysis]:
    if not feedback_ids:
        return {}
    stmt = select(FeedbackAnalysis).where(FeedbackAnalysis.feedback_id.in_(feedback_ids))
    return {row.feedback_id: row for row in db.execute(stmt).scalars()}


def find_similar_feedback(
    db: Session,
    feedback: Feedback,
    *,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
    similarity_threshold: float = DEFAULT_SIMILARITY_FLOOR,
) -> list[SimilarFeedbackItem]:
    """Finds semantic neighbors for one feedback item using pgvector cosine
    distance. Candidate retrieval — the expensive nearest-neighbor search —
    happens entirely in Postgres (ORDER BY + LIMIT on the `<=>` operator);
    this never loads the embedding table into Python to compare vectors
    itself, so it doesn't degrade into an O(n) or O(n^2) Python scan as
    feedback volume grows.

    Raises `EmbeddingMissingError` if `feedback` itself has no embedding yet.
    """
    limit = min(limit, MAX_CANDIDATE_LIMIT)

    source_embedding = get_embedding_for_feedback(db, feedback.id)
    if source_embedding is None:
        raise EmbeddingMissingError(
            f"Feedback {feedback.id} has no embedding yet. Call POST /embed first."
        )

    distance = FeedbackEmbedding.vector.cosine_distance(source_embedding.vector)
    max_distance = 1 - similarity_threshold

    stmt = (
        select(FeedbackEmbedding.feedback_id, distance.label("distance"))
        .where(FeedbackEmbedding.feedback_id != feedback.id)
        .where(distance <= max_distance)
        .order_by(distance)
        .limit(limit)
    )
    rows = db.execute(stmt).all()

    analyses = _load_analyses(db, [feedback.id, *(row.feedback_id for row in rows)])
    source_analysis = analyses.get(feedback.id)

    candidates = []
    for row in rows:
        similarity = max(-1.0, min(1.0, 1 - row.distance))
        relationship_type = classify_relationship(
            similarity, source_analysis, analyses.get(row.feedback_id)
        )
        candidates.append(
            SimilarFeedbackItem(
                feedback_id=row.feedback_id, similarity=similarity, relationship_type=relationship_type
            )
        )

    return candidates


def _normalize_pair(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    return (a, b) if a < b else (b, a)


def upsert_relationship(
    db: Session,
    feedback_id_x: uuid.UUID,
    feedback_id_y: uuid.UUID,
    relationship_type: RelationshipType,
    similarity: float,
) -> FeedbackRelationship:
    """Writes (or updates) the single row for this unordered pair. Never
    creates a second row for the reverse direction — see
    `FeedbackRelationship`'s docstring."""
    feedback_id_a, feedback_id_b = _normalize_pair(feedback_id_x, feedback_id_y)

    existing = db.execute(
        select(FeedbackRelationship).where(
            FeedbackRelationship.feedback_id_a == feedback_id_a,
            FeedbackRelationship.feedback_id_b == feedback_id_b,
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.relationship_type = relationship_type
        existing.similarity_score = similarity
        existing.algorithm_version = SEMANTIC_ANALYSIS_ALGORITHM_VERSION
        db.add(existing)
        return existing

    relationship = FeedbackRelationship(
        feedback_id_a=feedback_id_a,
        feedback_id_b=feedback_id_b,
        relationship_type=relationship_type,
        similarity_score=similarity,
        algorithm_version=SEMANTIC_ANALYSIS_ALGORITHM_VERSION,
    )
    db.add(relationship)
    return relationship


def run_semantic_analysis(
    db: Session,
    feedback: Feedback,
    *,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
    similarity_threshold: float = DEFAULT_SIMILARITY_FLOOR,
) -> tuple[list[SimilarFeedbackItem], list[FeedbackRelationship]]:
    """Finds candidates and persists a FeedbackRelationship row for every
    DUPLICATE/RELATED outcome (DISTINCT is never persisted — see
    `FeedbackRelationship`'s docstring). Returns (all candidates considered,
    the relationships written)."""
    candidates = find_similar_feedback(
        db, feedback, limit=limit, similarity_threshold=similarity_threshold
    )

    relationships = [
        upsert_relationship(db, feedback.id, candidate.feedback_id, candidate.relationship_type, candidate.similarity)
        for candidate in candidates
        if candidate.relationship_type != RelationshipType.DISTINCT
    ]

    db.commit()
    for relationship in relationships:
        db.refresh(relationship)

    logger.info(
        "semantic_analysis_complete id=%s candidates=%s persisted=%s",
        feedback.id,
        len(candidates),
        len(relationships),
    )
    return candidates, relationships

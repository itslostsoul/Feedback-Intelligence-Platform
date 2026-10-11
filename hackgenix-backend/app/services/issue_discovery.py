import logging
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.enums import FeedbackCategory, ProductIssueStatus, RelationshipType
from app.db.models.feedback_analysis import FeedbackAnalysis
from app.db.models.feedback_relationship import FeedbackRelationship
from app.db.models.product_issue import ProductIssue
from app.db.models.product_issue_feedback import ProductIssueFeedback

logger = logging.getLogger(__name__)

# A "product issue" requires at least this many corroborating feedback items
# — a single, unconfirmed complaint isn't yet a pattern worth promoting.
MIN_ISSUE_SIZE = 2

# Bump whenever MIN_ISSUE_SIZE, the cohesion filter, or the summarization
# logic below changes, so a ProductIssue row's `clustering_version` always
# identifies exactly which logic produced/last touched it (reproducibility).
CLUSTERING_ALGORITHM_VERSION = "cluster-v1"

# A clear majority category must hold *more than* this fraction of analyzed
# members before it's used to peel off a minority member as an outlier (see
# _apply_structured_cohesion_filter) — an exact tie (e.g. 1 of 2) has no real
# majority (Counter.most_common()'s pick among tied values is arbitrary), so
# nothing is removed in that case.
COHESION_MAJORITY_FRACTION = 0.5

MAX_TITLE_LENGTH = 140


@dataclass
class IssueDiscoveryResult:
    components_considered: int = 0
    issues_created: int = 0
    issues_updated: int = 0
    feedback_linked: int = 0
    feedback_left_unclustered: int = 0
    issues: list[ProductIssue] = field(default_factory=list)


class _UnionFind:
    """Minimal union-find over UUIDs. Building components this way is
    O(E * alpha(n)) in the number of relationship edges — not an O(n^2)
    pairwise comparison — since it only ever looks at edges that already
    exist in `feedback_relationship` (computed via pgvector in Phase 3B-2)."""

    def __init__(self) -> None:
        self._parent: dict[uuid.UUID, uuid.UUID] = {}

    def find(self, x: uuid.UUID) -> uuid.UUID:
        self._parent.setdefault(x, x)
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: uuid.UUID, b: uuid.UUID) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self._parent[root_a] = root_b


def _build_components(edges: list[tuple[uuid.UUID, uuid.UUID]]) -> list[set[uuid.UUID]]:
    """Connected components over the duplicate/related relationship graph.

    Why connected components instead of HDBSCAN: HDBSCAN over raw embeddings
    needs pairwise distances across the whole dataset (O(n^2), or an ANN
    index — Phase 3B-2 documented why that index is premature at current
    scale). We already have a curated, pgvector-computed pairwise graph from
    Phase 3B-2 that encodes exactly the "same underlying problem" signal
    HDBSCAN would otherwise have to rediscover from scratch; reusing it via
    connected components is a real, standard graph-clustering technique
    (equivalent to single-linkage clustering cut at "an edge exists"), keeps
    behavior consistent with what /semantic-analysis already decided, and
    needs no new dependency. HDBSCAN's real advantages — variable-density
    clusters, soft/probabilistic membership — remain a documented option for
    a future phase once data volume and an ANN index justify the cost.

    A feedback item with zero edges never appears in `edges` at all, so it's
    implicitly excluded here — outliers are never forced into a cluster.
    """
    if not edges:
        return []

    union_find = _UnionFind()
    nodes: set[uuid.UUID] = set()
    for a, b in edges:
        union_find.union(a, b)
        nodes.add(a)
        nodes.add(b)

    groups: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for node in nodes:
        groups[union_find.find(node)].add(node)
    return list(groups.values())


def _load_analyses(db: Session, feedback_ids: set[uuid.UUID]) -> dict[uuid.UUID, FeedbackAnalysis]:
    if not feedback_ids:
        return {}
    stmt = select(FeedbackAnalysis).where(FeedbackAnalysis.feedback_id.in_(feedback_ids))
    return {row.feedback_id: row for row in db.execute(stmt).scalars()}


def _apply_structured_cohesion_filter(
    member_ids: set[uuid.UUID], analyses: dict[uuid.UUID, FeedbackAnalysis]
) -> set[uuid.UUID]:
    """Raw embedding similarity chained across a connected component (A~B,
    B~C) doesn't guarantee A and C are actually the same issue — this is the
    classic weakness of connected-components/single-linkage clustering. This
    peels off members whose *analyzed* category clearly disagrees with the
    component's majority, rather than trusting similarity alone as proof.

    Members with no analysis are given the benefit of the doubt (kept) —
    there's nothing to validate them against yet, matching the "downgrade
    only when structured data is available" rule already used in
    app.services.semantic_engine.classify_relationship.
    """
    analyzed_categories = [
        analyses[member_id].category for member_id in member_ids if member_id in analyses
    ]
    if not analyzed_categories:
        return set(member_ids)

    category_counts = Counter(analyzed_categories)
    dominant_category, dominant_count = category_counts.most_common(1)[0]
    if dominant_count / len(analyzed_categories) <= COHESION_MAJORITY_FRACTION:
        # A tie (or worse) means Counter.most_common()'s pick is arbitrary,
        # not a real majority — nothing to filter against.
        return set(member_ids)

    return {
        member_id
        for member_id in member_ids
        if member_id not in analyses or analyses[member_id].category == dominant_category
    }


def _summarize_cluster(
    member_ids: set[uuid.UUID], analyses: dict[uuid.UUID, FeedbackAnalysis]
) -> tuple[str, FeedbackCategory | None, str | None]:
    """Deterministic title/category/affected_feature — explicitly acceptable
    for this phase per the Phase 3B-3 spec ("if deterministic naming is
    sufficient for this phase, that is acceptable"). Reuses the existing
    per-item analysis `summary` (already "one actionable sentence") as the
    issue title rather than inventing new text.

    A future phase could instead pass a compact representative set (e.g. one
    summary per distinct category+feature combination present) through the
    existing LLMProvider abstraction (app.services.llm.base.LLMProvider) for
    a more natural title — never one call per individual feedback item, and
    never asked to compute an aggregate metric.
    """
    analyzed = [analyses[m] for m in member_ids if m in analyses]
    if not analyzed:
        return f"Cluster of {len(member_ids)} related feedback items", None, None

    dominant_category = Counter(a.category for a in analyzed).most_common(1)[0][0]

    feature_counts = Counter(a.affected_feature.strip().lower() for a in analyzed)
    dominant_feature_key = feature_counts.most_common(1)[0][0]
    dominant_feature = next(
        a.affected_feature for a in analyzed if a.affected_feature.strip().lower() == dominant_feature_key
    )

    matching = [a for a in analyzed if a.category == dominant_category]
    title = min(matching, key=lambda a: a.feedback_id).summary.strip()
    if len(title) > MAX_TITLE_LENGTH:
        title = title[: MAX_TITLE_LENGTH - 3].rstrip() + "..."

    return title, dominant_category, dominant_feature


def _find_existing_issue_for_members(
    db: Session, member_ids: set[uuid.UUID]
) -> ProductIssue | None:
    """Finds "the same issue as before" by existing membership overlap,
    not by canonical_feedback_id — the canonical member could be filtered
    out of the cluster by the cohesion filter in a later rerun, so matching
    on it would break reproducibility across runs. This is a single indexed
    query, not a per-member lookup loop."""
    links = db.execute(
        select(ProductIssueFeedback).where(ProductIssueFeedback.feedback_id.in_(member_ids))
    ).scalars().all()
    if not links:
        return None

    issue_id, _ = Counter(link.product_issue_id for link in links).most_common(1)[0]
    return db.get(ProductIssue, issue_id)


def run_issue_discovery(db: Session) -> IssueDiscoveryResult:
    """Clusters existing `feedback_relationship` rows into ProductIssues.

    Reads whatever is currently in `feedback_relationship` — it does not
    itself call semantic-analysis. For discovery to find anything, Phase
    3B-2's /semantic-analysis must already have been run for the relevant
    feedback items. This keeps each phase's endpoint doing one thing: 3B-2
    decides pairwise relationships, 3B-3 clusters whatever relationships
    currently exist.

    Never deletes a feedback item or an existing FeedbackRelationship row.
    Membership only ever grows: a feedback item already linked to an issue
    is never unlinked or moved, even if it no longer clusters with that
    issue's other members in a later rerun — see the module docstring above
    for why (preserving historical traceability, per phase requirements).
    """
    edge_rows = db.execute(
        select(FeedbackRelationship.feedback_id_a, FeedbackRelationship.feedback_id_b).where(
            FeedbackRelationship.relationship_type.in_([RelationshipType.DUPLICATE, RelationshipType.RELATED])
        )
    ).all()
    components = _build_components([(row.feedback_id_a, row.feedback_id_b) for row in edge_rows])

    result = IssueDiscoveryResult(components_considered=len(components))

    for component in components:
        analyses = _load_analyses(db, component)
        filtered = _apply_structured_cohesion_filter(component, analyses)

        if len(filtered) < MIN_ISSUE_SIZE:
            result.feedback_left_unclustered += len(component)
            continue

        existing_issue = _find_existing_issue_for_members(db, filtered)
        title, category, affected_feature = _summarize_cluster(filtered, analyses)

        already_linked = {
            row.feedback_id
            for row in db.execute(
                select(ProductIssueFeedback.feedback_id).where(ProductIssueFeedback.feedback_id.in_(filtered))
            )
        }
        new_members = filtered - already_linked

        if existing_issue is not None:
            existing_issue.title = title
            existing_issue.category = category
            existing_issue.affected_feature = affected_feature
            existing_issue.clustering_version = CLUSTERING_ALGORITHM_VERSION
            db.add(existing_issue)
            issue = existing_issue
            result.issues_updated += 1
        else:
            issue = ProductIssue(
                canonical_feedback_id=min(filtered),
                title=title,
                category=category,
                affected_feature=affected_feature,
                status=ProductIssueStatus.OPEN,
                clustering_version=CLUSTERING_ALGORITHM_VERSION,
            )
            db.add(issue)
            db.flush()  # assign issue.id before creating links
            result.issues_created += 1

        for member_id in new_members:
            db.add(ProductIssueFeedback(product_issue_id=issue.id, feedback_id=member_id))
        result.feedback_linked += len(new_members)
        result.feedback_left_unclustered += len(component) - len(filtered)
        result.issues.append(issue)

    db.commit()
    for issue in result.issues:
        db.refresh(issue)

    logger.info(
        "issue_discovery_complete components=%s created=%s updated=%s linked=%s unclustered=%s",
        result.components_considered,
        result.issues_created,
        result.issues_updated,
        result.feedback_linked,
        result.feedback_left_unclustered,
    )
    return result


def get_issue(db: Session, issue_id: uuid.UUID) -> ProductIssue | None:
    return db.get(ProductIssue, issue_id)


def get_feedback_ids_for_issue(db: Session, issue_id: uuid.UUID) -> list[uuid.UUID]:
    stmt = select(ProductIssueFeedback.feedback_id).where(ProductIssueFeedback.product_issue_id == issue_id)
    return list(db.execute(stmt).scalars())


def count_feedback_for_issues(db: Session, issue_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not issue_ids:
        return {}
    stmt = (
        select(ProductIssueFeedback.product_issue_id, func.count())
        .where(ProductIssueFeedback.product_issue_id.in_(issue_ids))
        .group_by(ProductIssueFeedback.product_issue_id)
    )
    return dict(db.execute(stmt).all())


def list_issues(
    db: Session,
    *,
    limit: int,
    offset: int,
    status: ProductIssueStatus | None = None,
    category: FeedbackCategory | None = None,
) -> tuple[list[ProductIssue], int]:
    filters = []
    if status:
        filters.append(ProductIssue.status == status)
    if category:
        filters.append(ProductIssue.category == category)

    count_stmt = select(func.count()).select_from(ProductIssue)
    stmt = select(ProductIssue)
    for condition in filters:
        count_stmt = count_stmt.where(condition)
        stmt = stmt.where(condition)

    total = db.execute(count_stmt).scalar_one()
    stmt = stmt.order_by(ProductIssue.created_at.desc(), ProductIssue.id.desc()).limit(limit).offset(offset)
    items = list(db.execute(stmt).scalars())
    return items, total

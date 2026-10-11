import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.decision import Decision
from app.db.models.enums import MatchMethod, ProductIssueStatus
from app.db.models.product_issue import ProductIssue

# Bump whenever a weight, threshold, or the matching logic itself changes, so
# a persisted DecisionIssueEvidence row's match_method/match_score always
# reflects a known, identifiable version of this logic (same convention as
# clustering_version/algorithm_version/signal_version elsewhere).
MATCH_ALGORITHM_VERSION = "match-v1"

# --- Deterministic matching only ---------------------------------------------
# No LLM call, no NLP keyword extraction, no embeddings/semantic similarity
# (Phase 4A explicitly defers semantic matching — see docs/architecture.md's
# Phase 4A section for why). Decision.keywords are explicit, caller-provided
# tokens; this module only ever does case-insensitive exact/substring/token
# comparison against them.

# affected_feature is a clustering-normalized label (Phase 3B-3) — the
# highest-confidence field to match against. An exact match (after
# normalization) scores higher than a substring match.
AFFECTED_FEATURE_EXACT_WEIGHT = 1.0
AFFECTED_FEATURE_SUBSTRING_WEIGHT = 0.7

# title is a free-text representative summary sentence (Phase 3B-3), not a
# normalized label — token-overlap fraction against the decision's keywords,
# scaled down relative to affected_feature since it's a weaker signal.
TITLE_TOKEN_OVERLAP_WEIGHT = 0.5

# Category is NEVER a standalone match signal (only 5 FeedbackCategory
# values exist — far too coarse alone; every BUG issue would "match" any
# bug-related decision). It only ever boosts a score that's already > 0 from
# affected_feature/title matching — see match_issue() below, where the boost
# is applied strictly after the base-score-must-be-positive check.
CATEGORY_BOOST = 0.15

# Below this combined score, an issue is not linked to the decision at all —
# excluded entirely rather than stored as a weak/noisy match (per Phase 4A
# requirement: "weak matches ... excluded rather than stored as noise").
# Unvalidated hackathon-scale constant; see docs/architecture.md limitations.
MIN_MATCH_SCORE = 0.34

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def _normalize_keywords(keywords: list[str]) -> list[str]:
    return [kw.strip().lower() for kw in keywords if kw and kw.strip()]


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_PATTERN.findall(text.lower()))


@dataclass
class MatchCandidate:
    issue: ProductIssue
    method: MatchMethod
    score: float
    matched_terms: dict


def _score_affected_feature(
    keywords: list[str], affected_feature: str | None
) -> tuple[float, list[str], str | None]:
    """Returns (score, matched_keywords, match_kind). match_kind is "exact",
    "substring", or None. A keyword matches "exactly" if it equals the whole
    normalized affected_feature string; otherwise it matches as a substring
    in either direction (keyword contains the feature, or vice versa) —
    e.g. keyword "checkout" matches affected_feature "checkout flow"."""
    if not affected_feature:
        return 0.0, [], None

    normalized_feature = affected_feature.strip().lower()
    if not normalized_feature:
        return 0.0, [], None

    exact_matches = [kw for kw in keywords if kw == normalized_feature]
    if exact_matches:
        return AFFECTED_FEATURE_EXACT_WEIGHT, exact_matches, "exact"

    substring_matches = [
        kw for kw in keywords if kw in normalized_feature or normalized_feature in kw
    ]
    if substring_matches:
        return AFFECTED_FEATURE_SUBSTRING_WEIGHT, substring_matches, "substring"

    return 0.0, [], None


def _score_title(keywords: list[str], title: str) -> tuple[float, list[str]]:
    """Token-overlap fraction (matched keyword count / total keywords),
    scaled by TITLE_TOKEN_OVERLAP_WEIGHT."""
    if not keywords:
        return 0.0, []

    title_tokens = _tokenize(title)
    matched = [kw for kw in keywords if kw in title_tokens]
    if not matched:
        return 0.0, []

    overlap_fraction = len(matched) / len(keywords)
    return overlap_fraction * TITLE_TOKEN_OVERLAP_WEIGHT, matched


def match_issue(decision: Decision, issue: ProductIssue) -> MatchCandidate | None:
    """Deterministically scores one ProductIssue against one Decision.
    Returns None if there is no matching signal at all (score would be 0) —
    category alone can never produce a non-None result, since the boost is
    only ever applied on top of an already-positive base score.
    """
    keywords = _normalize_keywords(decision.keywords)
    if not keywords:
        return None

    feature_score, feature_matches, feature_match_kind = _score_affected_feature(
        keywords, issue.affected_feature
    )
    title_score, title_matches = _score_title(keywords, issue.title)

    base_score = feature_score + title_score
    if base_score <= 0:
        return None

    category_matched = decision.category is not None and issue.category == decision.category
    score = min(1.0, base_score + CATEGORY_BOOST) if category_matched else min(1.0, base_score)

    if feature_matches and title_matches:
        method = MatchMethod.AFFECTED_FEATURE_AND_TITLE
    elif feature_matches:
        method = MatchMethod.AFFECTED_FEATURE
    else:
        method = MatchMethod.TITLE

    reason_parts = []
    if feature_match_kind == "exact":
        reason_parts.append(f"affected_feature exactly matches keyword(s) {feature_matches}")
    elif feature_match_kind == "substring":
        reason_parts.append(f"affected_feature contains keyword(s) {feature_matches}")
    if title_matches:
        reason_parts.append(f"title contains keyword(s) {title_matches}")
    if category_matched:
        reason_parts.append(f"category matches requested category '{decision.category.value}' (boost only, not a standalone match)")

    matched_terms = {
        "keywords_evaluated": keywords,
        "affected_feature_matched_keywords": feature_matches,
        "affected_feature_match_kind": feature_match_kind,
        "title_matched_keywords": title_matches,
        "category_requested": decision.category.value if decision.category else None,
        "category_matched": category_matched,
        "reason": "; ".join(reason_parts),
    }

    return MatchCandidate(issue=issue, method=method, score=score, matched_terms=matched_terms)


def find_matching_issues(db: Session, decision: Decision) -> list[MatchCandidate]:
    """Scores every open ProductIssue against `decision` and returns those at
    or above MIN_MATCH_SCORE, ordered by score descending (ties broken by
    issue id for a stable, deterministic order). Only OPEN issues are
    considered — a decision about whether to prioritize something is
    inherently about currently-open problems.
    """
    issues = db.execute(
        select(ProductIssue).where(ProductIssue.status == ProductIssueStatus.OPEN)
    ).scalars().all()

    candidates = [
        candidate
        for issue in issues
        if (candidate := match_issue(decision, issue)) is not None and candidate.score >= MIN_MATCH_SCORE
    ]
    candidates.sort(key=lambda c: (-c.score, str(c.issue.id)))
    return candidates

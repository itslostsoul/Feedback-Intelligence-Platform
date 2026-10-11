import enum


class ProcessingStatus(str, enum.Enum):
    """Lifecycle of a feedback item as it moves through the analysis pipeline.

    RECEIVED          -> row persisted, nothing done yet
    PENDING_ANALYSIS   -> queued for AI understanding (future phase)
    ANALYZING          -> AI understanding in progress
    ANALYZED           -> AI understanding completed successfully
    NEEDS_REVIEW       -> analysis completed but flagged for human review
    FAILED             -> processing failed and needs attention / retry

    Kept as a flat enum (rather than a rigid linear state machine) so later
    phases can add transitions (e.g. re-analysis after correction) without
    a schema change — only new enum members.
    """

    RECEIVED = "received"
    PENDING_ANALYSIS = "pending_analysis"
    ANALYZING = "analyzing"
    ANALYZED = "analyzed"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class SourceType(str, enum.Enum):
    """Broad category of where a feedback item came from."""

    REVIEW = "review"
    SUPPORT_TICKET = "support_ticket"
    SURVEY = "survey"
    SOCIAL = "social"
    EMAIL = "email"
    INTERVIEW = "interview"
    OTHER = "other"


class FeedbackCategory(str, enum.Enum):
    """Initial product-feedback taxonomy used for LLM analysis (Phase 3A).

    Deliberately isolated here as its own enum, separate from any provider
    code, so the taxonomy can grow (new members) without touching the LLM
    layer that produces or consumes it.
    """

    BUG = "bug"
    FEATURE_REQUEST = "feature_request"
    UI_FRICTION = "ui_friction"
    PERFORMANCE = "performance"
    BILLING = "billing"


class RelationshipType(str, enum.Enum):
    """Deterministic classification of a semantic relationship between two
    feedback items (Phase 3B-2).

    DUPLICATE -> essentially the same feedback/content
    RELATED   -> different records describing the same/closely related
                 underlying product problem
    DISTINCT  -> a valid outcome of the classifier (not a relationship worth
                 acting on), but not currently persisted — see
                 app.services.semantic_engine and docs/architecture.md.

    Kept flat/extensible so a future phase can add finer distinctions (e.g.
    CONTRADICTS) without redesigning feedback_relationship.
    """

    DUPLICATE = "duplicate"
    RELATED = "related"
    DISTINCT = "distinct"


class ProductIssueStatus(str, enum.Enum):
    """Lifecycle of a discovered ProductIssue (Phase 3B-3).

    Only OPEN is ever set by the clustering code in this phase. RESOLVED and
    DISMISSED are reserved for a future human-review phase (marking an issue
    fixed, or marking a cluster as not actually a real issue) — kept here now
    so that phase doesn't need a schema change, only new usage of an
    already-existing enum member.
    """

    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class DecisionStatus(str, enum.Enum):
    """Lifecycle of a Decision (Phase 4A).

    Only OPEN is ever set by this phase. ANSWERED and ARCHIVED are reserved
    for a future phase (Phase 4B's LLM synthesis marking a decision as
    having a synthesized answer; a human archiving a stale question) — kept
    here now so that phase doesn't need a schema change.
    """

    OPEN = "open"
    ANSWERED = "answered"
    ARCHIVED = "archived"


class MatchMethod(str, enum.Enum):
    """Which deterministic signal(s) caused a ProductIssue to be linked to a
    Decision during matching (Phase 4A). Never a probability/confidence —
    see MATCH_ALGORITHM_VERSION and match_score's docstring in
    app.services.decision_matching for what match_score does and does not
    mean.

    AFFECTED_FEATURE  -> Decision.keywords matched ProductIssue.affected_feature
    TITLE             -> Decision.keywords matched ProductIssue.title
    AFFECTED_FEATURE_AND_TITLE -> both matched
    Category is never a standalone method — it only ever filters/boosts one
    of the above (see decision_matching.py); there is deliberately no
    CATEGORY member here.
    """

    AFFECTED_FEATURE = "affected_feature"
    TITLE = "title"
    AFFECTED_FEATURE_AND_TITLE = "affected_feature_and_title"


class EvidenceSufficiency(str, enum.Enum):
    """Backend-authoritative characterization of how much Phase 4A evidence
    backs a Decision's matched issues (Phase 4B). Computed deterministically
    from persisted DecisionIssueEvidence rows BEFORE any LLM call — the LLM
    is never trusted to determine this, and this value is never influenced
    by what the LLM writes (see app.services.decision_brief.compute_evidence_sufficiency).

    NO_MATCHES    -> No ProductIssues matched the decision at all (the
                     evaluation's decision_level_gaps contains only
                     no_matching_issues_found).
    INSUFFICIENT  -> At least one issue matched, but none of the matched
                     issues has any supporting or counter fact (only gaps,
                     or nothing at all) — not enough to meaningfully
                     describe the case for or against the issue.
    SUFFICIENT    -> At least one matched issue has at least one supporting
                     or counter fact.
    """

    NO_MATCHES = "no_matches"
    INSUFFICIENT = "insufficient"
    SUFFICIENT = "sufficient"


class GenerationStatus(str, enum.Enum):
    """Outcome of one DecisionBrief generation attempt (Phase 4B).

    No NEEDS_REVIEW-style middle state, unlike ProcessingStatus — unlike
    per-item FeedbackAnalysis (which has a continuous confidence float to
    threshold against), grounding validation here is binary: every citation
    resolves, or the whole generation is rejected. There's no natural
    "low-confidence but not wrong" state to represent.
    """

    COMPLETED = "completed"
    FAILED = "failed"

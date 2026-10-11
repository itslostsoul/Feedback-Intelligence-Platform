import uuid
from datetime import datetime

from pydantic import BaseModel


class SeveritySignal(BaseModel):
    """Full distribution, never reduced to a single number. Bucket
    boundaries: low 1-3, medium 4-6, high 7-8, critical 9-10."""

    distribution: dict[str, int]
    buckets: dict[str, int]
    mean: float | None
    median: float | None
    analyzed_count: int
    unanalyzed_count: int


class RecencySignal(BaseModel):
    reference_at: str
    latest_feedback_at: str | None
    oldest_feedback_at: str | None
    days_since_latest: int | None
    days_since_oldest: int | None


class TrendSignal(BaseModel):
    window_days: int
    current_window_count: int
    previous_window_count: int
    sufficient_data: bool
    min_sample_required: int
    direction: str


class CustomerReachSignal(BaseModel):
    distinct_known_customers: int
    known_feedback_count: int
    anonymous_feedback_count: int
    note: str


class SourceDiversitySignal(BaseModel):
    distinct_sources: int
    source_distribution: dict[str, int]
    source_type_distribution: dict[str, int]


class SegmentCoverageSignal(BaseModel):
    customer_tier_distribution: dict[str, int]
    unknown_tier_count: int


class DuplicateRatioSignal(BaseModel):
    duplicate_pair_count: int
    related_pair_count: int
    total_relationship_pairs: int
    possible_pairs: int
    duplicate_ratio: float | None
    relationship_coverage: float | None


class TimeDistributionBucket(BaseModel):
    period_start: str
    count: int


class TimeDistributionSignal(BaseModel):
    granularity: str
    buckets: list[TimeDistributionBucket]


class EvidenceQualitySignal(BaseModel):
    """Component flags only — never a single blended score."""

    source_diversity_sufficient: bool
    customer_reach_known: bool
    severity_data_available: bool
    severity_analyzed_fraction: float | None
    duplicate_concentration: str
    recency: str
    segment_coverage_known: bool
    evaluated_at: str


class ProductIssueSignalRead(BaseModel):
    id: uuid.UUID
    product_issue_id: uuid.UUID
    signal_version: str
    reference_at: datetime
    calculated_at: datetime
    feedback_count: int
    severity: SeveritySignal
    recency: RecencySignal
    trend: TrendSignal
    customer_reach: CustomerReachSignal
    source_diversity: SourceDiversitySignal
    segment_coverage: SegmentCoverageSignal
    duplicate_ratio: DuplicateRatioSignal
    time_distribution: TimeDistributionSignal
    evidence_quality: EvidenceQualitySignal

    model_config = {"from_attributes": True}


class IssueSignalsBulkRecalculateResponse(BaseModel):
    signal_version: str
    issues_processed: int

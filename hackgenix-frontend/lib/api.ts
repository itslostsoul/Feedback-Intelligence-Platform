/**
 * Typed client for the Hackgenix Feedback Intelligence API.
 *
 * Mirrors the contract in `hackgenix-backend/docs/frontend-integration.md`
 * (verified directly against the backend's Pydantic schemas / routers).
 * Base URL is configurable via NEXT_PUBLIC_API_BASE_URL; defaults to the
 * documented local dev address.
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/+$/, "") || "http://localhost:8000";

// ---------------------------------------------------------------------------
// Enums (mirrors app/db/models/enums.py)
// ---------------------------------------------------------------------------

export type ProcessingStatus =
  | "received"
  | "pending_analysis"
  | "analyzing"
  | "analyzed"
  | "needs_review"
  | "failed";

export type SourceType =
  | "review"
  | "support_ticket"
  | "survey"
  | "social"
  | "email"
  | "interview"
  | "other";

export type FeedbackCategory = "bug" | "feature_request" | "ui_friction" | "performance" | "billing";

export type RelationshipType = "duplicate" | "related" | "distinct";

export type ProductIssueStatus = "open" | "resolved" | "dismissed";

export type DecisionStatus = "open" | "answered" | "archived";

export type MatchMethod = "affected_feature" | "title" | "affected_feature_and_title";

export type EvidenceSufficiency = "no_matches" | "insufficient" | "sufficient";

export type GenerationStatus = "completed" | "failed";

export type IssueStabilityStatus = "new" | "removed" | "no_change" | "strengthened" | "weakened" | "mixed";

export type DecisionStabilityStatus =
  | "insufficient_history"
  | "stable"
  | "strengthened"
  | "weakened"
  | "mixed";

export type ChangeEventType =
  | "supporting_fact_added"
  | "supporting_fact_removed"
  | "counter_fact_added"
  | "counter_fact_removed"
  | "gap_added"
  | "gap_resolved"
  | "decision_level_gap_added"
  | "decision_level_gap_resolved"
  | "trend_changed"
  | "match_relevance_changed"
  | "value_changed"
  | "issue_appeared"
  | "issue_disappeared";

export type ChangeInterpretation =
  | "increased"
  | "decreased"
  | "changed"
  | "not_evaluated"
  | "newly_available"
  | "unavailable"
  | "newly_present"
  | "no_longer_present"
  | "appeared"
  | "disappeared"
  | "direction_reversed"
  | "transitioned";

// ---------------------------------------------------------------------------
// Shared shapes
// ---------------------------------------------------------------------------

export interface Paginated<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface EvidenceFact {
  code: string;
  label: string;
  value: unknown;
  threshold: number | null;
  source_signal_path: string | null;
}

// ---------------------------------------------------------------------------
// Feedback (Phase 2 / 3A)
// ---------------------------------------------------------------------------

export interface FeedbackCreate {
  external_id?: string | null;
  source: string;
  source_type?: SourceType;
  customer_id?: string | null;
  customer_tier?: string | null;
  raw_text: string;
  language?: string | null;
  created_at?: string | null;
  metadata?: Record<string, unknown> | null;
}

export interface FeedbackRead {
  id: string;
  external_id: string | null;
  source: string;
  source_type: SourceType;
  customer_id: string | null;
  customer_tier: string | null;
  raw_text: string;
  language: string | null;
  created_at: string | null;
  ingested_at: string;
  updated_at: string;
  processing_status: ProcessingStatus;
  metadata: Record<string, unknown> | null;
}

export type FeedbackListResponse = Paginated<FeedbackRead>;

export interface FeedbackAnalysisRead {
  id: string;
  feedback_id: string;
  category: FeedbackCategory;
  affected_feature: string;
  severity: number;
  confidence: number;
  summary: string;
  provider: string;
  model: string;
  analyzed_at: string;
}

export interface BulkIngestRowError {
  row: number;
  message: string;
}

export interface BulkIngestResponse {
  total_rows: number;
  accepted: number;
  duplicates: number;
  rejected: number;
  created_ids: string[];
  errors: BulkIngestRowError[];
}

export interface FeedbackEmbeddingRead {
  id: string;
  feedback_id: string;
  dimension: number;
  provider: string;
  model: string;
  created_at: string;
  updated_at: string;
}

/** Never includes the raw vector — see the backend schema's docstring. */
export interface EmbedFeedbackResponse {
  feedback_id: string;
  embedding: FeedbackEmbeddingRead;
  was_generated: boolean;
}

export interface FeedbackRelationshipRead {
  id: string;
  feedback_id_a: string;
  feedback_id_b: string;
  relationship_type: RelationshipType;
  similarity_score: number;
  algorithm_version: string;
  created_at: string;
  updated_at: string;
}

/** POST .../semantic-analysis: finds neighbors and persists a relationship
 * row for every DUPLICATE/RELATED candidate found. */
export interface SemanticAnalysisResponse {
  feedback_id: string;
  algorithm_version: string;
  candidates_considered: number;
  relationships: FeedbackRelationshipRead[];
}

// ---------------------------------------------------------------------------
// Product issues + signals (Phase 3B-3 / 3C)
// ---------------------------------------------------------------------------

export interface ProductIssueRead {
  id: string;
  canonical_feedback_id: string;
  title: string;
  category: FeedbackCategory | null;
  affected_feature: string | null;
  status: ProductIssueStatus;
  clustering_version: string;
  feedback_count: number;
  created_at: string;
  updated_at: string;
}

export interface ProductIssueDetail extends ProductIssueRead {
  feedback_ids: string[];
}

export type ProductIssueListResponse = Paginated<ProductIssueRead>;

export interface SeveritySignal {
  distribution: Record<string, number>;
  buckets: Record<string, number>;
  mean: number | null;
  median: number | null;
  analyzed_count: number;
  unanalyzed_count: number;
}

export interface RecencySignal {
  reference_at: string;
  latest_feedback_at: string | null;
  oldest_feedback_at: string | null;
  days_since_latest: number | null;
  days_since_oldest: number | null;
}

export interface TrendSignal {
  window_days: number;
  current_window_count: number;
  previous_window_count: number;
  sufficient_data: boolean;
  min_sample_required: number;
  direction: string;
}

export interface CustomerReachSignal {
  distinct_known_customers: number;
  known_feedback_count: number;
  anonymous_feedback_count: number;
  note: string;
}

export interface SourceDiversitySignal {
  distinct_sources: number;
  source_distribution: Record<string, number>;
  source_type_distribution: Record<string, number>;
}

export interface SegmentCoverageSignal {
  customer_tier_distribution: Record<string, number>;
  unknown_tier_count: number;
}

export interface DuplicateRatioSignal {
  duplicate_pair_count: number;
  related_pair_count: number;
  total_relationship_pairs: number;
  possible_pairs: number;
  duplicate_ratio: number | null;
  relationship_coverage: number | null;
}

export interface TimeDistributionBucket {
  period_start: string;
  count: number;
}

export interface TimeDistributionSignal {
  granularity: string;
  buckets: TimeDistributionBucket[];
}

export interface EvidenceQualitySignal {
  source_diversity_sufficient: boolean;
  customer_reach_known: boolean;
  severity_data_available: boolean;
  severity_analyzed_fraction: number | null;
  duplicate_concentration: string;
  recency: string;
  segment_coverage_known: boolean;
  evaluated_at: string;
}

export interface ProductIssueSignalRead {
  id: string;
  product_issue_id: string;
  signal_version: string;
  reference_at: string;
  calculated_at: string;
  feedback_count: number;
  severity: SeveritySignal;
  recency: RecencySignal;
  trend: TrendSignal;
  customer_reach: CustomerReachSignal;
  source_diversity: SourceDiversitySignal;
  segment_coverage: SegmentCoverageSignal;
  duplicate_ratio: DuplicateRatioSignal;
  time_distribution: TimeDistributionSignal;
  evidence_quality: EvidenceQualitySignal;
}

export interface IssueDiscoveryResponse {
  clustering_version: string;
  components_considered: number;
  issues_created: number;
  issues_updated: number;
  feedback_linked: number;
  feedback_left_unclustered: number;
}

// ---------------------------------------------------------------------------
// Decisions / evaluations / briefs (Phase 4A-4C)
// ---------------------------------------------------------------------------

export interface DecisionCreate {
  query_text: string;
  keywords: string[];
  category?: FeedbackCategory | null;
  affected_feature_hint?: string | null;
}

export interface DecisionRead {
  id: string;
  query_text: string;
  keywords: string[];
  category: FeedbackCategory | null;
  affected_feature_hint: string | null;
  status: DecisionStatus;
  created_at: string;
  updated_at: string;
}

export type DecisionListResponse = Paginated<DecisionRead>;

export interface DecisionIssueEvidenceRead {
  id: string;
  decision_evaluation_id: string;
  product_issue_id: string;
  product_issue_signal_snapshot_id: string | null;
  match_method: MatchMethod;
  match_score: number;
  matched_terms: Record<string, unknown>;
  volume_summary: Record<string, unknown>;
  supporting: EvidenceFact[];
  counter: EvidenceFact[];
  gaps: EvidenceFact[];
  created_at: string;
  issue_title: string | null;
  issue_category: FeedbackCategory | null;
  issue_affected_feature: string | null;
  issue_status: ProductIssueStatus | null;
}

export interface DecisionEvaluationRead {
  id: string;
  decision_id: string;
  evaluation_version: string;
  reference_at: string;
  created_at: string;
  decision_level_gaps: EvidenceFact[];
  issues: DecisionIssueEvidenceRead[];
}

export interface DecisionEvaluationSummary {
  id: string;
  decision_id: string;
  evaluation_version: string;
  reference_at: string;
  created_at: string;
  decision_level_gaps: EvidenceFact[];
  matched_issue_count: number;
}

export type DecisionEvaluationListResponse = Paginated<DecisionEvaluationSummary>;

export interface NarrativeClaim {
  text: string;
  citation_ids: string[];
}

export interface ResolvedCitation {
  citation_id: string;
  product_issue_id: string | null;
  issue_title: string | null;
  code: string;
  label: string;
  value: unknown;
  threshold: number | null;
  source_signal_path: string | null;
  category: "supporting" | "counter" | "gap";
}

export interface DecisionBriefDetail {
  id: string;
  decision_evaluation_id: string;
  status: GenerationStatus;
  evidence_sufficiency: EvidenceSufficiency;
  headline: string | null;
  supporting_narrative: NarrativeClaim[] | null;
  counter_narrative: NarrativeClaim[] | null;
  gap_narrative: NarrativeClaim[] | null;
  provider: string;
  model: string;
  generation_version: string;
  error_code: string | null;
  generated_at: string;
  created_at: string;
  citations: Record<string, ResolvedCitation>;
}

export interface DecisionBriefSummary {
  id: string;
  decision_evaluation_id: string;
  status: GenerationStatus;
  evidence_sufficiency: EvidenceSufficiency;
  provider: string;
  model: string;
  generation_version: string;
  error_code: string | null;
  generated_at: string;
  created_at: string;
}

export type DecisionBriefListResponse = Paginated<DecisionBriefSummary>;

export interface ChangeEvent {
  type: ChangeEventType;
  product_issue_id: string | null;
  fact_code: string | null;
  bucket: "supporting" | "counter" | "gaps" | null;
  previous_value: unknown;
  current_value: unknown;
  source_signal_path: string | null;
  interpretation: ChangeInterpretation;
}

export interface IssueStabilityResult {
  product_issue_id: string;
  status: IssueStabilityStatus;
  events: ChangeEvent[];
}

export interface DecisionStabilityResult {
  stability_version: string;
  status: DecisionStabilityStatus;
  evaluations_available: number;
  previous_evaluation_id: string | null;
  current_evaluation_id: string | null;
  previous_reference_at: string | null;
  current_reference_at: string | null;
  previous_evaluation_version: string | null;
  current_evaluation_version: string | null;
  evaluation_version_mismatch: boolean;
  reference_at_tie: boolean;
  issue_changes: IssueStabilityResult[];
  decision_level_events: ChangeEvent[];
  events: ChangeEvent[];
}

export interface ChallengeIssueSummary {
  product_issue_id: string;
  title: string | null;
  category: FeedbackCategory | null;
  affected_feature: string | null;
  status: string | null;
}

export interface IssueChallengeSummary {
  product_issue_id: string;
  status: IssueStabilityStatus;
  events: ChangeEvent[];
}

export interface DecisionChallengeResult {
  stability_version: string;
  status: DecisionStabilityStatus;
  evaluations_available: number;
  previous_evaluation_id: string | null;
  current_evaluation_id: string | null;
  previous_reference_at: string | null;
  current_reference_at: string | null;
  previous_evaluation_version: string | null;
  current_evaluation_version: string | null;
  evaluation_version_mismatch: boolean;
  reference_at_tie: boolean;
  supporting_changes: ChangeEvent[];
  counter_changes: ChangeEvent[];
  trend_changes: ChangeEvent[];
  gap_changes: ChangeEvent[];
  relevance_changes: ChangeEvent[];
  value_changes: ChangeEvent[];
  new_issues: ChangeEvent[];
  disappeared_issues: ChangeEvent[];
  decision_level_changes: ChangeEvent[];
  issues: IssueChallengeSummary[];
  issue_summaries: Record<string, ChallengeIssueSummary>;
  all_events: ChangeEvent[];
}

// ---------------------------------------------------------------------------
// Analytics (deterministic aggregates + grounded AI insight)
// ---------------------------------------------------------------------------

export interface RecentFeedbackTrend {
  window_days: number;
  current_window_count: number;
  previous_window_count: number;
  sufficient_data: boolean;
  min_sample_required: number;
  direction: string;
}

export interface AnalyticsIssueVolumeItem {
  issue_id: string;
  title: string;
  feedback_count: number;
}

export interface AnalyticsIssueTrendItem {
  issue_id: string;
  title: string;
  direction: string;
  sufficient_data: boolean;
  current_window_count: number;
  previous_window_count: number;
}

export interface TopIssueItem {
  issue_id: string;
  title: string;
  category: FeedbackCategory | null;
  affected_feature: string | null;
  status: ProductIssueStatus;
  feedback_count: number;
  signals_calculated: boolean;
  average_severity: number | null;
  high_severity_count: number | null;
  distinct_known_customers: number | null;
  trend_direction: string | null;
  duplicate_ratio: number | null;
  distinct_sources: number | null;
}

export interface TopIssuesResponse {
  items: TopIssueItem[];
  total: number;
  ranking_version: string;
  ranking_method: string;
}

export interface AnalyticsOverview {
  total_feedback: number;
  analyzed_feedback: number;
  unanalyzed_feedback: number;
  analysis_coverage: number | null;
  total_product_issues: number;
  open_product_issues: number;
  resolved_product_issues: number;
  average_severity: number | null;
  high_severity_feedback_count: number;
  unique_customers: number;
  source_distribution: Record<string, number>;
  category_distribution: Record<string, number>;
  issue_volume: AnalyticsIssueVolumeItem[];
  recent_feedback_trend: RecentFeedbackTrend;
  issue_trends: AnalyticsIssueTrendItem[];
  top_issues: TopIssueItem[];
  calculated_at: string;
}

export interface DailyTrendPoint {
  date: string;
  feedback_count: number;
  analyzed_count: number;
  high_severity_count: number;
}

export interface AnalyticsTrendsResponse {
  window_days: number;
  points: DailyTrendPoint[];
  earliest_feedback_at: string | null;
  days_with_data: number;
  sufficient_history: boolean;
  min_days_for_reliable_trend: number;
  message: string | null;
}

export interface CategoryCount {
  category: string;
  count: number;
  percentage: number | null;
}

export interface AnalyticsCategoriesResponse {
  total_analyzed: number;
  items: CategoryCount[];
}

export interface SourceCount {
  source: string;
  feedback_count: number;
  analyzed_count: number;
  percentage: number;
}

export interface AnalyticsSourcesResponse {
  total_feedback: number;
  items: SourceCount[];
}

export interface AnalyticsEvidenceFact {
  citation_id: string;
  label: string;
  value: unknown;
}

export interface InsightClaim {
  text: string;
  evidence_ids: string[];
}

export interface AnalyticsInsightResult {
  headline: string;
  key_findings: InsightClaim[];
  emerging_patterns: InsightClaim[];
  areas_to_investigate: InsightClaim[];
  caveats: InsightClaim[];
  provider: string;
  model: string;
  insight_version: string;
  generated_at: string;
  evidence: Record<string, AnalyticsEvidenceFact>;
}

// ---------------------------------------------------------------------------
// Error envelope + fetch wrapper
// ---------------------------------------------------------------------------

/** Mirrors {"error": {"code": "...", "message": "..."}} from app/core/errors.py */
export class ApiError extends Error {
  code: string;
  status: number;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/** Thrown when the backend can't be reached at all (offline, CORS, DNS...). */
export class ApiUnreachableError extends Error {
  constructor(cause?: unknown) {
    super("Could not reach the Hackgenix backend. Is it running?");
    this.name = "ApiUnreachableError";
    if (cause) this.cause = cause;
  }
}

async function request<T>(
  path: string,
  init?: RequestInit & { query?: Record<string, string | number | boolean | undefined | null> }
): Promise<T> {
  const { query, ...rest } = init ?? {};
  const url = new URL(`${API_BASE_URL}${path}`);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    }
  }

  let response: Response;
  try {
    response = await fetch(url.toString(), {
      ...rest,
      headers: {
        Accept: "application/json",
        ...(rest.body ? { "Content-Type": "application/json" } : {}),
        ...rest.headers,
      },
    });
  } catch (err) {
    throw new ApiUnreachableError(err);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  const data = text ? JSON.parse(text) : undefined;

  if (!response.ok) {
    const code = data?.error?.code ?? "unknown_error";
    const message = data?.error?.message ?? `Request failed with status ${response.status}`;
    throw new ApiError(response.status, code, message);
  }

  return data as T;
}

function get<T>(path: string, query?: Record<string, string | number | boolean | undefined | null>) {
  return request<T>(path, { method: "GET", query });
}

function post<T>(path: string, body?: unknown, query?: Record<string, string | number | boolean | undefined | null>) {
  return request<T>(path, {
    method: "POST",
    body: body !== undefined ? JSON.stringify(body) : undefined,
    query,
  });
}

// ---------------------------------------------------------------------------
// Public API surface, grouped exactly as docs/frontend-integration.md §3
// ---------------------------------------------------------------------------

export const api = {
  health: {
    liveness: () => get<{ status: string }>("/"),
    ok: () => get<{ status: string }>("/api/v1/health"),
    db: () => get<{ status: string }>("/api/v1/health/db"),
  },

  feedback: {
    create: (payload: FeedbackCreate) => post<FeedbackRead>("/api/v1/feedback", payload),
    get: (id: string) => get<FeedbackRead>(`/api/v1/feedback/${id}`),
    list: (params?: {
      limit?: number;
      offset?: number;
      source?: string;
      source_type?: SourceType;
      status?: ProcessingStatus;
    }) => get<FeedbackListResponse>("/api/v1/feedback", params),
    bulkIngest: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch(`${API_BASE_URL}/api/v1/feedback/bulk`, {
        method: "POST",
        body: form,
      });
      const data = await response.json();
      if (!response.ok) {
        throw new ApiError(response.status, data?.error?.code ?? "unknown_error", data?.error?.message ?? "Bulk ingest failed");
      }
      return data as BulkIngestResponse;
    },
    analyze: (id: string) => post<{ feedback_id: string; processing_status: ProcessingStatus; analysis: FeedbackAnalysisRead }>(
      `/api/v1/feedback/${id}/analyze`
    ),
    getAnalysis: (id: string) => get<FeedbackAnalysisRead>(`/api/v1/feedback/${id}/analysis`),
    embed: (id: string) => post<EmbedFeedbackResponse>(`/api/v1/feedback/${id}/embed`),
    semanticAnalysis: (id: string) => post<SemanticAnalysisResponse>(`/api/v1/feedback/${id}/semantic-analysis`),
  },

  issues: {
    discover: () => post<IssueDiscoveryResponse>("/api/v1/issues/discover"),
    recalculateAllSignals: () => post<{ signal_version: string; issues_processed: number }>("/api/v1/issues/signals/recalculate"),
    list: (params?: { limit?: number; offset?: number; status?: ProductIssueStatus; category?: FeedbackCategory }) =>
      get<ProductIssueListResponse>("/api/v1/issues", params),
    get: (id: string) => get<ProductIssueDetail>(`/api/v1/issues/${id}`),
    recalculateSignals: (id: string) => post<ProductIssueSignalRead>(`/api/v1/issues/${id}/signals/recalculate`),
    getSignals: (id: string) => get<ProductIssueSignalRead>(`/api/v1/issues/${id}/signals`),
  },

  decisions: {
    create: (payload: DecisionCreate) => post<DecisionRead>("/api/v1/decisions", payload),
    list: (params?: { limit?: number; offset?: number }) => get<DecisionListResponse>("/api/v1/decisions", params),
    get: (id: string) => get<DecisionRead>(`/api/v1/decisions/${id}`),
    evaluate: (id: string) => post<DecisionEvaluationRead>(`/api/v1/decisions/${id}/evaluate`),
    listEvaluations: (id: string, params?: { limit?: number; offset?: number }) =>
      get<DecisionEvaluationListResponse>(`/api/v1/decisions/${id}/evaluations`, params),
    latestEvaluation: (id: string) => get<DecisionEvaluationRead>(`/api/v1/decisions/${id}/evaluations/latest`),
    getEvaluation: (id: string, evaluationId: string) =>
      get<DecisionEvaluationRead>(`/api/v1/decisions/${id}/evaluations/${evaluationId}`),
    generateBrief: (id: string) => post<DecisionBriefDetail>(`/api/v1/decisions/${id}/brief`),
    latestBrief: (id: string) => get<DecisionBriefDetail>(`/api/v1/decisions/${id}/brief`),
    briefHistory: (id: string, params?: { limit?: number; offset?: number }) =>
      get<DecisionBriefListResponse>(`/api/v1/decisions/${id}/brief/history`, params),
    getBrief: (id: string, briefId: string) => get<DecisionBriefDetail>(`/api/v1/decisions/${id}/brief/${briefId}`),
    stability: (id: string) => get<DecisionStabilityResult>(`/api/v1/decisions/${id}/stability`),
    compareEvaluations: (id: string, fromId: string, toId: string) =>
      get<DecisionStabilityResult>(`/api/v1/decisions/${id}/evaluations/${fromId}/compare/${toId}`),
    challenge: (id: string) => get<DecisionChallengeResult>(`/api/v1/decisions/${id}/challenge`),
  },

  analytics: {
    overview: () => get<AnalyticsOverview>("/api/v1/analytics/overview"),
    trends: (days = 30) => get<AnalyticsTrendsResponse>("/api/v1/analytics/trends", { days }),
    categories: () => get<AnalyticsCategoriesResponse>("/api/v1/analytics/categories"),
    sources: () => get<AnalyticsSourcesResponse>("/api/v1/analytics/sources"),
    issues: (params?: { limit?: number }) => get<TopIssuesResponse>("/api/v1/analytics/issues", params),
    generateInsights: () => post<AnalyticsInsightResult>("/api/v1/analytics/insights"),
  },
};

/** Human-readable message for any error this client can throw. */
export function describeApiError(err: unknown): string {
  if (err instanceof ApiUnreachableError) return err.message;
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Something went wrong.";
}

/**
 * Deterministic keywords for an on-the-fly Decision that is guaranteed to
 * match the given issue under app/services/decision_matching.py's rules:
 * an exact affected_feature keyword scores 1.0, and a single title token
 * scores TITLE_TOKEN_OVERLAP_WEIGHT (0.5) — both comfortably clear
 * MIN_MATCH_SCORE (0.34).
 */
export function deriveKeywordsForIssue(issue: ProductIssueRead | ProductIssueDetail): string[] {
  if (issue.affected_feature && issue.affected_feature.trim()) {
    return [issue.affected_feature.trim()];
  }
  const tokens = issue.title.toLowerCase().match(/[a-z0-9]+/g) ?? [];
  const stopwords = new Set(["the", "a", "an", "is", "are", "on", "in", "to", "of", "for", "and", "or"]);
  const longest = tokens
    .filter((t) => !stopwords.has(t))
    .sort((a, b) => b.length - a.length)[0];
  return longest ? [longest] : [issue.title.trim()];
}

"use client";

import { useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  deriveKeywordsForIssue,
  describeApiError as errorMessage,
  type DecisionChallengeResult,
  type DecisionBriefDetail,
  type DecisionEvaluationRead,
  type DecisionIssueEvidenceRead,
  type FeedbackRead,
  type ProductIssueRead,
  type ProductIssueSignalRead,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  categoryBadgeClass,
  categoryLabel,
  formatPercent,
  formatValue,
  issueStatusLabel,
  sourceTypeLabel,
} from "@/lib/format";

const PIPELINE_STAGES = ["Signal", "Cluster", "Evidence", "Decision"];

type SignalsState =
  | { status: "loading" }
  | { status: "missing" }
  | { status: "ready"; data: ProductIssueSignalRead }
  | { status: "error"; message: string };

type BriefState =
  | { status: "loading" }
  | { status: "unavailable"; message: string }
  | { status: "ready"; data: DecisionBriefDetail };

export default function DecisionBrief({ issue }: { issue: ProductIssueRead | null }) {
  const decisionCache = useRef<Map<string, string>>(new Map());
  const activeIssueId = useRef<string | null>(null);

  const [decisionId, setDecisionId] = useState<string | null>(null);
  const [evaluation, setEvaluation] = useState<DecisionEvaluationRead | null>(null);
  const [loadStage, setLoadStage] = useState<string | null>(null);
  const [orchestrationError, setOrchestrationError] = useState<string | null>(null);

  const [brief, setBrief] = useState<BriefState>({ status: "loading" });
  const [signals, setSignals] = useState<SignalsState>({ status: "loading" });

  const [expandedCitation, setExpandedCitation] = useState<string | null>(null);

  const [showSources, setShowSources] = useState(false);
  const [sources, setSources] = useState<FeedbackRead[] | null>(null);
  const [sourcesLoading, setSourcesLoading] = useState(false);
  const [sourcesError, setSourcesError] = useState<string | null>(null);

  const [showChallenge, setShowChallenge] = useState(false);
  const [challenge, setChallenge] = useState<DecisionChallengeResult | null>(null);
  const [challengeLoading, setChallengeLoading] = useState(false);
  const [challengeError, setChallengeError] = useState<string | null>(null);

  useEffect(() => {
    if (!issue) return;
    const currentIssue = issue;
    activeIssueId.current = currentIssue.id;

    // Everything for this issue selection — the state reset included —
    // happens inside one nested function the effect declares and
    // immediately invokes (the canonical data-fetching-effect pattern),
    // so no setState call executes synchronously in the effect body itself.
    function resetAndLoad() {
      setDecisionId(null);
      setEvaluation(null);
      setOrchestrationError(null);
      setBrief({ status: "loading" });
      setSignals({ status: "loading" });
      setShowSources(false);
      setSources(null);
      setSourcesError(null);
      setShowChallenge(false);
      setChallenge(null);
      setChallengeError(null);
      setExpandedCitation(null);

      const stillActive = () => activeIssueId.current === currentIssue.id;

      // Signals are independent of the decision workflow — fetch in parallel.
      api.issues
        .getSignals(currentIssue.id)
        .then((data) => stillActive() && setSignals({ status: "ready", data }))
        .catch((err) => {
          if (!stillActive()) return;
          if (err instanceof ApiError && err.code === "signals_missing") {
            setSignals({ status: "missing" });
          } else {
            setSignals({ status: "error", message: errorMessage(err) });
          }
        });

      async function loadBrief(id: string) {
        setBrief({ status: "loading" });
        try {
          const existing = await api.decisions.latestBrief(id);
          if (stillActive()) setBrief({ status: "ready", data: existing });
          return;
        } catch (err) {
          if (!(err instanceof ApiError && err.code === "not_found")) {
            if (stillActive()) setBrief({ status: "unavailable", message: errorMessage(err) });
            return;
          }
        }
        try {
          const generated = await api.decisions.generateBrief(id);
          if (stillActive()) setBrief({ status: "ready", data: generated });
        } catch (err) {
          if (stillActive()) setBrief({ status: "unavailable", message: errorMessage(err) });
        }
      }

      async function orchestrate() {
        try {
          let id = decisionCache.current.get(currentIssue.id) ?? null;
          let evalResult: DecisionEvaluationRead;

          if (!id) {
            setLoadStage("Creating decision…");
            const decision = await api.decisions.create({
              query_text: `Should we prioritize addressing "${currentIssue.title}"?`,
              keywords: deriveKeywordsForIssue(currentIssue),
              category: currentIssue.category,
              affected_feature_hint: currentIssue.affected_feature,
            });
            id = decision.id;
            decisionCache.current.set(currentIssue.id, id);

            setLoadStage("Evaluating evidence…");
            evalResult = await api.decisions.evaluate(id);
          } else {
            setLoadStage("Loading latest evaluation…");
            try {
              evalResult = await api.decisions.latestEvaluation(id);
            } catch (err) {
              if (err instanceof ApiError && err.code === "not_found") {
                evalResult = await api.decisions.evaluate(id);
              } else {
                throw err;
              }
            }
          }

          if (!stillActive()) return;
          setDecisionId(id);
          setEvaluation(evalResult);
          setLoadStage(null);

          await loadBrief(id);
        } catch (err) {
          if (stillActive()) {
            setOrchestrationError(errorMessage(err));
            setLoadStage(null);
          }
        }
      }

      orchestrate();
    }

    resetAndLoad();
  }, [issue]);

  if (!issue) return null;

  const matchedEvidence: DecisionIssueEvidenceRead | undefined = evaluation?.issues.find(
    (row) => row.product_issue_id === issue.id
  );

  async function handleChallenge() {
    if (!decisionId) return;
    setChallengeLoading(true);
    setChallengeError(null);
    try {
      await api.decisions.evaluate(decisionId);
      const [freshEvaluation, result] = await Promise.all([
        api.decisions.latestEvaluation(decisionId),
        api.decisions.challenge(decisionId),
      ]);
      setEvaluation(freshEvaluation);
      setChallenge(result);
      setShowChallenge(true);

      setBrief({ status: "loading" });
      try {
        const generated = await api.decisions.generateBrief(decisionId);
        setBrief({ status: "ready", data: generated });
      } catch (err) {
        setBrief({ status: "unavailable", message: errorMessage(err) });
      }
    } catch (err) {
      setChallengeError(errorMessage(err));
    } finally {
      setChallengeLoading(false);
    }
  }

  async function toggleSources() {
    if (showSources) {
      setShowSources(false);
      return;
    }
    setShowSources(true);
    if (sources || sourcesLoading) return;
    setSourcesLoading(true);
    setSourcesError(null);
    try {
      const detail = await api.issues.get(issue!.id);
      const ids = detail.feedback_ids.slice(0, 8);
      const items = await Promise.all(ids.map((id) => api.feedback.get(id)));
      setSources(items);
    } catch (err) {
      setSourcesError(errorMessage(err));
    } finally {
      setSourcesLoading(false);
    }
  }

  async function handleRecalculateSignals() {
    setSignals({ status: "loading" });
    try {
      const data = await api.issues.recalculateSignals(issue!.id);
      setSignals({ status: "ready", data });
    } catch (err) {
      setSignals({ status: "error", message: errorMessage(err) });
    }
  }

  return (
    <div className="flex h-screen w-full flex-col overflow-y-auto bg-card p-8 text-foreground md:p-10">
      {/* pipeline breadcrumb */}
      <div className="mb-8 flex items-center gap-2 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
        {PIPELINE_STAGES.map((stage, i) => (
          <span key={stage} className="flex items-center gap-2">
            <span className={i === 3 ? "font-semibold text-chart-2" : undefined}>{stage}</span>
            {i < PIPELINE_STAGES.length - 1 && <span className="text-border">/</span>}
          </span>
        ))}
      </div>

      {/* header */}
      <div className="mb-10">
        <div className="mb-4 flex items-center gap-3 font-mono text-[10px] text-muted-foreground">
          <span>{issue.id.slice(0, 8)}</span>
          <span className="h-1 w-1 rounded-full bg-border" />
          <span>Product issue</span>
        </div>
        <h2 className="mb-6 text-2xl font-semibold leading-tight tracking-tight md:text-[1.7rem]">
          {issue.title}
        </h2>
        <div className="flex flex-wrap gap-2">
          <Badge variant="outline" className={`rounded-sm font-medium ${categoryBadgeClass(issue.category)}`}>
            {categoryLabel(issue.category)}
          </Badge>
          <Badge variant="outline" className="rounded-sm bg-muted font-mono text-muted-foreground">
            {issueStatusLabel(issue.status)}
          </Badge>
          {issue.affected_feature && (
            <Badge variant="outline" className="rounded-sm bg-muted font-mono text-muted-foreground">
              {issue.affected_feature}
            </Badge>
          )}
        </div>
      </div>

      {orchestrationError && (
        <div className="mb-8 rounded-lg border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
          Couldn&rsquo;t evaluate this decision: {orchestrationError}
        </div>
      )}

      {/* metrics — real, per-issue signal data (never a fabricated blended score) */}
      <div className="mb-10 grid grid-cols-2 gap-4 border-b border-border pb-10 sm:grid-cols-4 md:gap-6">
        <div>
          <p className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">Feedback</p>
          <p className="font-mono text-xl md:text-2xl">{issue.feedback_count}</p>
        </div>
        <div>
          <p className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">Known customers</p>
          <p className="font-mono text-xl md:text-2xl">
            {signals.status === "ready" ? signals.data.customer_reach.distinct_known_customers : "—"}
          </p>
        </div>
        <div>
          <p className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">Duplicate ratio</p>
          <p className="font-mono text-xl text-chart-1 md:text-2xl">
            {signals.status === "ready" ? formatPercent(signals.data.duplicate_ratio.duplicate_ratio) : "—"}
          </p>
        </div>
        <div>
          <p className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">Trend</p>
          <p className="font-mono text-xl text-chart-2 md:text-2xl">
            {signals.status === "ready"
              ? signals.data.trend.sufficient_data
                ? signals.data.trend.direction
                : "N/A"
              : "—"}
          </p>
        </div>
      </div>

      {signals.status === "missing" && (
        <div className="mb-8 flex items-center justify-between rounded-lg border border-border bg-background/60 p-4 text-sm text-muted-foreground">
          <span>Signals haven&rsquo;t been calculated for this issue yet.</span>
          <Button variant="outline" size="sm" onClick={handleRecalculateSignals}>
            Calculate signals
          </Button>
        </div>
      )}
      {signals.status === "error" && (
        <div className="mb-8 rounded-lg border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
          Couldn&rsquo;t load signals: {signals.message}
        </div>
      )}

      {/* evidence */}
      <div className="mb-10">
        <h3 className="mb-4 text-[11px] uppercase tracking-wider text-muted-foreground">
          Evidence-backed decision brief
        </h3>

        {loadStage && <p className="text-sm text-muted-foreground">{loadStage}</p>}

        {!loadStage && brief.status === "loading" && (
          <p className="text-sm text-muted-foreground">Generating grounded brief…</p>
        )}

        {!loadStage && brief.status === "ready" && (
          <div className="space-y-6">
            <p className="font-medium leading-relaxed">{brief.data.headline}</p>
            <Badge variant="outline" className="rounded-sm bg-muted font-mono text-[10px] uppercase text-muted-foreground">
              Evidence: {brief.data.evidence_sufficiency.replace("_", " ")}
            </Badge>

            <NarrativeSection
              title="Supporting"
              claims={brief.data.supporting_narrative}
              citations={brief.data.citations}
              expandedCitation={expandedCitation}
              onToggleCitation={setExpandedCitation}
              accent="text-chart-2"
            />
            <NarrativeSection
              title="Counter"
              claims={brief.data.counter_narrative}
              citations={brief.data.citations}
              expandedCitation={expandedCitation}
              onToggleCitation={setExpandedCitation}
              accent="text-chart-1"
            />
            <NarrativeSection
              title="Gaps"
              claims={brief.data.gap_narrative}
              citations={brief.data.citations}
              expandedCitation={expandedCitation}
              onToggleCitation={setExpandedCitation}
              accent="text-muted-foreground"
            />
          </div>
        )}

        {!loadStage && brief.status === "unavailable" && (
          <div className="space-y-6">
            <p className="text-sm text-muted-foreground">
              AI-written brief unavailable ({brief.message}). Showing the underlying deterministic evidence instead.
            </p>
            {matchedEvidence ? (
              <div className="space-y-5">
                <p className="font-mono text-[11px] text-muted-foreground">
                  Matched via {matchedEvidence.match_method.replace(/_/g, " ")} · score{" "}
                  {matchedEvidence.match_score.toFixed(2)}
                </p>
                <FactList title="Supporting facts" facts={matchedEvidence.supporting} accent="text-chart-2" />
                <FactList title="Counter facts" facts={matchedEvidence.counter} accent="text-chart-1" />
                <FactList title="Gaps" facts={matchedEvidence.gaps} accent="text-muted-foreground" />
              </div>
            ) : evaluation ? (
              <p className="text-sm text-muted-foreground">
                No evidence facts matched this issue under the current deterministic rules.
              </p>
            ) : null}
          </div>
        )}
      </div>

      {/* challenge panel */}
      {showChallenge && challenge && (
        <div className="mb-10 rounded-lg border border-border bg-background/60 p-5">
          <div className="mb-4 flex items-center justify-between">
            <h4 className="text-[10px] uppercase tracking-wider text-chart-2">Challenge my decision</h4>
            <Badge variant="outline" className="rounded-sm bg-muted font-mono text-[10px] uppercase text-muted-foreground">
              {challenge.status.replace("_", " ")}
            </Badge>
          </div>
          {challenge.status === "insufficient_history" ? (
            <p className="text-sm text-muted-foreground">
              Only one evaluation exists so far. Challenge again later to see what changed.
            </p>
          ) : (
            <div className="space-y-4">
              <ChangeBucket title="New issues" events={challenge.new_issues} summaries={challenge.issue_summaries} />
              <ChangeBucket
                title="Disappeared issues"
                events={challenge.disappeared_issues}
                summaries={challenge.issue_summaries}
              />
              <ChangeBucket
                title="Supporting changes"
                events={challenge.supporting_changes}
                summaries={challenge.issue_summaries}
              />
              <ChangeBucket
                title="Counter changes"
                events={challenge.counter_changes}
                summaries={challenge.issue_summaries}
              />
              <ChangeBucket title="Trend changes" events={challenge.trend_changes} summaries={challenge.issue_summaries} />
              <ChangeBucket title="Gap changes" events={challenge.gap_changes} summaries={challenge.issue_summaries} />
              {challenge.all_events.length === 0 && (
                <p className="text-sm text-muted-foreground">No evidence changes since the previous evaluation.</p>
              )}
            </div>
          )}
        </div>
      )}
      {challengeError && (
        <div className="mb-8 rounded-lg border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
          Couldn&rsquo;t challenge this decision: {challengeError}
        </div>
      )}

      {/* raw sources */}
      {showSources && (
        <div className="mb-8 rounded-lg border border-border bg-background/60 p-5">
          <h4 className="mb-3 text-[10px] uppercase tracking-wider text-chart-2">Raw source records</h4>
          {sourcesLoading && <p className="text-sm text-muted-foreground">Loading raw feedback…</p>}
          {sourcesError && <p className="text-sm text-destructive">{sourcesError}</p>}
          {sources && (
            <ul className="space-y-3 text-sm text-muted-foreground">
              {sources.length === 0 ? (
                <li>No individual records are linked to this cluster yet.</li>
              ) : (
                sources.map((source) => (
                  <li key={source.id} className="border-l-2 border-border pl-3">
                    <span className="mr-2 font-mono text-foreground/70">{source.id.slice(0, 8)}</span>
                    <span className="mr-2 font-mono text-[10px] uppercase text-chart-2">
                      {sourceTypeLabel(source.source_type)} · {source.source}
                    </span>
                    &ldquo;{source.raw_text}&rdquo;
                  </li>
                ))
              )}
            </ul>
          )}
        </div>
      )}

      {/* actions */}
      <div className="mt-auto flex gap-3 border-t border-border pt-8">
        <Button onClick={handleChallenge} disabled={challengeLoading || !decisionId} className="flex-1" size="lg">
          {challengeLoading ? "Re-evaluating…" : "Challenge this decision"}
        </Button>
        <Button onClick={toggleSources} variant="outline" className="flex-1" size="lg">
          {showSources ? "Hide raw sources" : "View raw sources"}
        </Button>
      </div>
    </div>
  );
}

function NarrativeSection({
  title,
  claims,
  citations,
  expandedCitation,
  onToggleCitation,
  accent,
}: {
  title: string;
  claims: DecisionBriefDetail["supporting_narrative"];
  citations: DecisionBriefDetail["citations"];
  expandedCitation: string | null;
  onToggleCitation: (id: string | null) => void;
  accent: string;
}) {
  if (!claims || claims.length === 0) return null;
  return (
    <div>
      <p className={`mb-2 text-[10px] uppercase tracking-wider ${accent}`}>{title}</p>
      <ul className="space-y-3">
        {claims.map((claim, i) => (
          <li key={i} className="leading-relaxed text-muted-foreground">
            {claim.text}{" "}
            {claim.citation_ids.map((id) => (
              <button
                key={id}
                onClick={() => onToggleCitation(expandedCitation === id ? null : id)}
                className="ml-1 rounded-sm bg-muted px-1.5 py-0.5 font-mono text-[10px] text-foreground hover:bg-muted/70"
              >
                [{citations[id]?.code ?? id}]
              </button>
            ))}
            {claim.citation_ids
              .filter((id) => id === expandedCitation)
              .map((id) => {
                const c = citations[id];
                if (!c) return null;
                return (
                  <div
                    key={id}
                    className="mt-2 rounded-sm border border-border bg-background/60 p-3 font-mono text-xs text-foreground/80"
                  >
                    <p>{c.label}</p>
                    <p className="text-muted-foreground">
                      value: {formatValue(c.value)}
                      {c.threshold !== null && ` · threshold: ${c.threshold}`}
                    </p>
                  </div>
                );
              })}
          </li>
        ))}
      </ul>
    </div>
  );
}

function FactList({
  title,
  facts,
  accent,
}: {
  title: string;
  facts: DecisionIssueEvidenceRead["supporting"];
  accent: string;
}) {
  if (facts.length === 0) return null;
  return (
    <div>
      <p className={`mb-2 text-[10px] uppercase tracking-wider ${accent}`}>{title}</p>
      <ul className="space-y-2 text-sm text-muted-foreground">
        {facts.map((fact) => (
          <li key={fact.code} className="border-l-2 border-border pl-3">
            <span className="mr-2 font-mono text-foreground/80">{fact.label}</span>
            <span className="font-mono text-xs">
              {formatValue(fact.value)}
              {fact.threshold !== null && ` (threshold ${fact.threshold})`}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ChangeBucket({
  title,
  events,
  summaries,
}: {
  title: string;
  events: DecisionChallengeResult["all_events"];
  summaries: DecisionChallengeResult["issue_summaries"];
}) {
  if (events.length === 0) return null;
  return (
    <div>
      <p className="mb-2 text-[10px] uppercase tracking-wider text-muted-foreground">{title}</p>
      <ul className="space-y-2 text-sm text-muted-foreground">
        {events.map((event, i) => {
          const issueTitle = event.product_issue_id ? summaries[event.product_issue_id]?.title : null;
          return (
            <li key={i} className="border-l-2 border-border pl-3">
              {issueTitle && <span className="mr-2 font-medium text-foreground">{issueTitle}</span>}
              <span className="font-mono text-xs">
                {event.fact_code ?? event.type} · {event.interpretation.replace(/_/g, " ")}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

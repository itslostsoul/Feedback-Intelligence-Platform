"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  api,
  ApiError,
  deriveKeywordsForIssue,
  describeApiError as errorMessage,
  type DecisionChallengeResult,
  type DecisionBriefDetail,
  type DecisionEvaluationRead,
  type DecisionIssueEvidenceRead,
  type DecisionStabilityResult,
  type FeedbackRead,
  type ProductIssueRead,
  type ProductIssueSignalRead,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import PipelineStepper, { type PipelineStage } from "@/components/ui/PipelineStepper";
import { Inbox, Layers, FileSearch, Gavel, ShieldAlert, Sparkles } from "lucide-react";
import {
  categoryLabel,
  formatPercent,
  formatRelativeTime,
  formatValue,
  issueStatusLabel,
  sourceTypeLabel,
} from "@/lib/format";

type SignalsState =
  | { status: "loading" }
  | { status: "missing" }
  | { status: "ready"; data: ProductIssueSignalRead }
  | { status: "error"; message: string };

type BriefState =
  | { status: "loading" }
  | { status: "unavailable"; message: string }
  | { status: "ready"; data: DecisionBriefDetail };

type StabilityState =
  | { status: "loading" }
  | { status: "ready"; data: DecisionStabilityResult }
  | { status: "error"; message: string };

type ChallengeState =
  | { status: "loading" }
  | { status: "ready"; data: DecisionChallengeResult }
  | { status: "error"; message: string };

/** Case/order-insensitive equality check for two Decision.keywords arrays. */
function keywordsFingerprint(keywords: string[]): string {
  return [...keywords].map((k) => k.trim().toLowerCase()).filter(Boolean).sort().join("|");
}

/**
 * Looks for an existing Decision whose keywords exactly match what we'd
 * derive for this issue — e.g. the demo seed's "Should we prioritize fixing
 * checkout?" decision (keywords: ["checkout"]) for the "Checkout" issue.
 * Reusing it (rather than always creating a fresh Decision) is what keeps a
 * decision's real evaluation history — and therefore Stability/Challenge —
 * intact across repeated visits instead of starting over every time.
 */
async function findExistingDecisionId(keywords: string[]): Promise<string | null> {
  const target = keywordsFingerprint(keywords);
  if (!target) return null;
  const page = await api.decisions.list({ limit: 100 });
  const match = page.items.find((d) => keywordsFingerprint(d.keywords) === target);
  return match ? match.id : null;
}

export default function DecisionBrief({
  issue,
  onClose,
}: {
  issue: ProductIssueRead | null;
  /** Closes the panel this component is rendered inside — used by the
   * "Raw Signals" / "Product Issues" pipeline stages, which navigate away
   * from this issue's investigation workspace entirely. */
  onClose?: () => void;
}) {
  const router = useRouter();
  const decisionCache = useRef<Map<string, string>>(new Map());
  const activeIssueId = useRef<string | null>(null);
  const signalsSectionRef = useRef<HTMLDivElement>(null);
  const evidenceSectionRef = useRef<HTMLDivElement>(null);
  const decisionSectionRef = useRef<HTMLDivElement>(null);
  const challengeSectionRef = useRef<HTMLDivElement>(null);

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

  const [showFullSignals, setShowFullSignals] = useState(false);

  const [showStability, setShowStability] = useState(false);
  const [stability, setStability] = useState<StabilityState>({ status: "loading" });

  const [showChallenge, setShowChallenge] = useState(false);
  const [challengeState, setChallengeState] = useState<ChallengeState>({ status: "loading" });

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
      setShowFullSignals(false);
      setShowStability(false);
      setStability({ status: "loading" });
      setShowChallenge(false);
      setChallengeState({ status: "loading" });
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

      // Read-only; independent of the brief workflow. Fetched unconditionally
      // so the Stability/Challenge panels open instantly, and refetchable via
      // the same function from each panel's own "Refresh" control — both are
      // GETs derived from already-persisted evaluations, never a mutation.
      async function loadStabilityAndChallenge(id: string) {
        setStability({ status: "loading" });
        setChallengeState({ status: "loading" });
        try {
          const data = await api.decisions.stability(id);
          if (stillActive()) setStability({ status: "ready", data });
        } catch (err) {
          if (stillActive()) setStability({ status: "error", message: errorMessage(err) });
        }
        try {
          const data = await api.decisions.challenge(id);
          if (stillActive()) setChallengeState({ status: "ready", data });
        } catch (err) {
          if (stillActive()) setChallengeState({ status: "error", message: errorMessage(err) });
        }
      }

      async function orchestrate() {
        try {
          let id = decisionCache.current.get(currentIssue.id) ?? null;

          if (!id) {
            const keywords = deriveKeywordsForIssue(currentIssue);
            setLoadStage("Looking for an existing decision…");
            id = await findExistingDecisionId(keywords);

            if (!id) {
              setLoadStage("Creating decision…");
              const decision = await api.decisions.create({
                query_text: `Should we prioritize addressing "${currentIssue.title}"?`,
                keywords,
                category: currentIssue.category,
                affected_feature_hint: currentIssue.affected_feature,
              });
              id = decision.id;
            }
            decisionCache.current.set(currentIssue.id, id);
          }

          setLoadStage("Loading latest evaluation…");
          let evalResult: DecisionEvaluationRead;
          try {
            evalResult = await api.decisions.latestEvaluation(id);
          } catch (err) {
            if (err instanceof ApiError && err.code === "not_found") {
              // First time this decision has ever been evaluated — the only
              // place this component calls POST .../evaluate automatically;
              // once history exists, viewing Stability/Challenge never
              // appends a new evaluation of its own accord.
              setLoadStage("Evaluating evidence…");
              evalResult = await api.decisions.evaluate(id);
            } else {
              throw err;
            }
          }

          if (!stillActive()) return;
          setDecisionId(id);
          setEvaluation(evalResult);
          setLoadStage(null);

          loadStabilityAndChallenge(id);
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

  // Signal/Cluster navigate to the existing pages that own those pipeline
  // stages, closing this panel first — otherwise "Cluster" (which routes to
  // "/", the page this panel is already open on top of) had no visible
  // effect, since a same-URL router.push doesn't touch this Sheet's own
  // open state. Evidence/Decision/Challenge scroll within THIS panel — it
  // already IS the evidence, decision and challenge experience, so there's
  // no separate route to send them to (and none should be invented). Plain
  // named handlers (rather than closures built into a data array) so ref
  // access stays inside a recognizable event-handler function.
  function goToSignalStage() {
    onClose?.();
    router.push("/firehose");
  }
  function goToClusterStage() {
    onClose?.();
    router.push("/");
  }
  function scrollToEvidenceStage() {
    evidenceSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function scrollToDecisionStage() {
    decisionSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function scrollToChallengeStage() {
    setShowStability(true);
    setShowChallenge(true);
    requestAnimationFrame(() => challengeSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }

  // Both are pure GETs over already-persisted evaluations — refreshing them
  // never appends a new DecisionEvaluation (that only ever happens once,
  // automatically, the first time a decision is opened — see orchestrate()).
  async function refreshStability() {
    if (!decisionId) return;
    setStability({ status: "loading" });
    try {
      const data = await api.decisions.stability(decisionId);
      setStability({ status: "ready", data });
    } catch (err) {
      setStability({ status: "error", message: errorMessage(err) });
    }
  }

  async function refreshChallenge() {
    if (!decisionId) return;
    setChallengeState({ status: "loading" });
    try {
      const data = await api.decisions.challenge(decisionId);
      setChallengeState({ status: "ready", data });
    } catch (err) {
      setChallengeState({ status: "error", message: errorMessage(err) });
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

  // The pipeline this investigation workspace walks through. "Signals" is
  // deliberately not a stage here — it's an internal evidence input, not a
  // navigation destination — but the underlying signal data is still shown
  // in its own section below. Every node reuses one of the goTo/scrollTo
  // handlers above — nothing here is a fabricated action. "active" reflects
  // real, already-fetched state, not a cosmetic guess.
  const pipelineStages: PipelineStage[] = [
    { key: "raw-signals", label: "Raw signals", icon: Inbox, onClick: goToSignalStage },
    { key: "product-issues", label: "Product issues", icon: Layers, onClick: goToClusterStage },
    { key: "evidence", label: "Evidence", icon: FileSearch, onClick: scrollToEvidenceStage, active: brief.status === "ready" },
    { key: "decision", label: "Decision", icon: Gavel, onClick: scrollToDecisionStage, active: brief.status === "ready" && !!brief.data?.headline },
    { key: "challenge", label: "Challenge / stability", icon: ShieldAlert, onClick: scrollToChallengeStage, active: showStability || showChallenge },
  ];

  return (
    <div className="flex h-screen w-full flex-col overflow-y-auto bg-surface-3 text-foreground">
      {/* Sticky so these controls stay reachable (and clickable) no matter
          how far the user has scrolled into the investigation workspace —
          previously they scrolled away with the content, which made
          "clicking Evidence/Decision does nothing" look like a broken
          handler when it was really just an unreachable button. */}
      <div className="sticky top-0 z-10 flex-none border-b border-border bg-surface-3/95 backdrop-blur supports-backdrop-filter:bg-surface-3/85">
        {/* pipeline breadcrumb — each stage is a real, functional action; see
            the goTo/scrollTo handlers above for what each one does and why */}
        <div className="flex items-center gap-2 px-8 py-3 text-xs text-muted-foreground md:px-10">
          <span className="flex items-center gap-1.5">
            <button onClick={goToSignalStage} className="px-1 py-0.5 transition-colors hover:text-foreground">
              Signal
            </button>
            <span className="text-border">/</span>
          </span>
          <span className="flex items-center gap-1.5">
            <button onClick={goToClusterStage} className="px-1 py-0.5 transition-colors hover:text-foreground">
              Cluster
            </button>
            <span className="text-border">/</span>
          </span>
          <span className="flex items-center gap-1.5">
            <button onClick={scrollToEvidenceStage} className="px-1 py-0.5 transition-colors hover:text-foreground">
              Evidence
            </button>
            <span className="text-border">/</span>
          </span>
          <span className="flex items-center gap-1.5">
            <button onClick={scrollToDecisionStage} className="px-1 py-0.5 font-medium text-brass transition-colors hover:text-brass/80">
              Decision
            </button>
          </span>
        </div>

        {/* pipeline stepper — the full end-to-end workflow this workspace
            walks through, made visually explicit rather than implied */}
        <div className="overflow-x-auto border-t border-border px-6 py-1 md:px-8">
          <PipelineStepper stages={pipelineStages} />
        </div>
      </div>

      <div className="flex flex-1 flex-col p-8 md:p-10">
        {/* header */}
        <div className="mb-8">
          <p className="mb-3 text-xs font-medium uppercase tracking-wide text-brass">Product issue</p>
          <h2 className="mb-4 font-serif text-3xl leading-tight tracking-tight text-foreground">
            {issue.title}
          </h2>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
            <span className="font-medium uppercase tracking-wide text-foreground">{categoryLabel(issue.category)}</span>
            <span className="text-border">·</span>
            <span className="uppercase tracking-wide">{issueStatusLabel(issue.status)}</span>
            {issue.affected_feature && (
              <>
                <span className="text-border">·</span>
                <span className="font-mono">{issue.affected_feature}</span>
              </>
            )}
            <span className="font-mono text-muted-foreground">#{issue.id.slice(0, 8)}</span>
          </div>
        </div>

        {/* compact evidence summary — the same real signal data as the
            SIGNALS section below, surfaced immediately so the reader gets
            the headline numbers before scrolling */}
        <div className="mb-10 grid grid-cols-2 gap-6 border-y border-border py-5 sm:grid-cols-4">
          <div>
            <p className="font-mono text-xl font-medium text-foreground">{issue.feedback_count}</p>
            <p className="mt-0.5 text-xs text-muted-foreground">Feedback</p>
          </div>
          <div>
            <p className="font-mono text-xl font-medium text-foreground">
              {signals.status === "ready" ? signals.data.customer_reach.distinct_known_customers : "—"}
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">Customers</p>
          </div>
          <div>
            <p className="font-mono text-xl font-medium text-warning">
              {signals.status === "ready" ? formatPercent(signals.data.duplicate_ratio.duplicate_ratio) : "—"}
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">Duplicate</p>
          </div>
          <div>
            <p className="font-mono text-xl font-medium text-chart-2">
              {signals.status === "ready"
                ? signals.data.trend.sufficient_data
                  ? signals.data.trend.direction
                  : "N/A"
                : "—"}
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">Trend</p>
          </div>
        </div>

        {orchestrationError && (
          <div className="mb-8 rounded-lg border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
            Couldn&rsquo;t evaluate this decision: {orchestrationError}
          </div>
        )}

        {/* signals — real, per-issue signal data (never a fabricated blended score) */}
        <div ref={signalsSectionRef} className="mb-10">
          <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-foreground">Signals</h3>

          {signals.status === "ready" && (
            <>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
                <SignalTile label="Frequency" value={issue.feedback_count} />
                <SignalTile
                  label="Severity (mean)"
                  value={formatValue(signals.data.severity.mean)}
                  accent="text-warning"
                />
                <SignalTile
                  label="Customer reach"
                  value={signals.data.customer_reach.distinct_known_customers}
                />
                <SignalTile
                  label="Trend"
                  value={signals.data.trend.sufficient_data ? signals.data.trend.direction : "N/A"}
                  accent="text-chart-2"
                />
                <SignalTile label="Source diversity" value={signals.data.source_diversity.distinct_sources} />
              </div>

              <button
                onClick={() => setShowFullSignals((v) => !v)}
                className="mt-4 text-xs font-medium text-muted-foreground hover:text-foreground"
              >
                {showFullSignals ? "Hide full signal snapshot" : "Show full signal snapshot"}
              </button>
              {showFullSignals && (
                <div className="mt-4 grid grid-cols-2 gap-4 border border-border bg-surface-2 p-4 text-sm sm:grid-cols-3">
                  <div>
                    <p className="text-xs text-muted-foreground">Severity (mean / median)</p>
                    <p className="font-mono">
                      {formatValue(signals.data.severity.mean)} / {formatValue(signals.data.severity.median)}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">Analyzed / unanalyzed</p>
                    <p className="font-mono">
                      {signals.data.severity.analyzed_count} / {signals.data.severity.unanalyzed_count}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">Distinct sources</p>
                    <p className="font-mono">{signals.data.source_diversity.distinct_sources}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">Anonymous feedback</p>
                    <p className="font-mono">{signals.data.customer_reach.anonymous_feedback_count}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">Unknown customer tier</p>
                    <p className="font-mono">{signals.data.segment_coverage.unknown_tier_count}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">Days since latest feedback</p>
                    <p className="font-mono">{formatValue(signals.data.recency.days_since_latest)}</p>
                  </div>
                  <div className="col-span-2 sm:col-span-3">
                    <p className="mb-1 text-xs text-muted-foreground">Evidence quality flags</p>
                    <p className="font-mono text-xs text-muted-foreground">
                      source diversity sufficient: {String(signals.data.evidence_quality.source_diversity_sufficient)} ·
                      customer reach known: {String(signals.data.evidence_quality.customer_reach_known)} · severity data
                      available: {String(signals.data.evidence_quality.severity_data_available)} · duplicate
                      concentration: {signals.data.evidence_quality.duplicate_concentration} · recency:{" "}
                      {signals.data.evidence_quality.recency}
                    </p>
                  </div>
                </div>
              )}
            </>
          )}

          {signals.status === "missing" && (
            <div className="flex items-center justify-between border border-border bg-surface-2 p-4 text-sm text-muted-foreground">
              <span>Signals haven&rsquo;t been calculated for this issue yet.</span>
              <Button variant="outline" size="sm" onClick={handleRecalculateSignals}>
                Calculate signals
              </Button>
            </div>
          )}
          {signals.status === "error" && (
            <div className="border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
              Couldn&rsquo;t load signals: {signals.message}
            </div>
          )}
        </div>

        {/* decision — the synthesized headline this issue's evidence supports;
            "decision section" per the pipeline stepper / breadcrumb above */}
        <div ref={decisionSectionRef} className="mb-10 border-b border-border pb-10">
          <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-foreground">Decision</h3>

          {loadStage && <p className="text-sm text-muted-foreground">{loadStage}</p>}

          {!loadStage && brief.status === "loading" && (
            <p className="text-sm text-muted-foreground">Generating grounded brief…</p>
          )}

          {!loadStage && brief.status === "ready" && (
            <div>
              <div className="mb-3 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-brass">
                <Sparkles className="h-3.5 w-3.5" /> AI interpretation
              </div>
              <p className="mb-4 max-w-[64ch] font-serif text-xl italic leading-snug text-foreground">
                &ldquo;{brief.data.headline}&rdquo;
              </p>
              <Badge variant="outline" className="rounded-full bg-surface-4 font-mono text-xs text-muted-foreground">
                Evidence: {brief.data.evidence_sufficiency.replace("_", " ")}
              </Badge>
            </div>
          )}

          {!loadStage && brief.status === "unavailable" && (
            <p className="text-sm text-muted-foreground">
              AI-written decision unavailable ({brief.message}). See the evidence below for the underlying
              deterministic facts.
            </p>
          )}
        </div>

        {/* evidence — the supporting/counter/gap facts the decision above is
            grounded in; "evidence section" per the pipeline stepper / breadcrumb */}
        <div ref={evidenceSectionRef} className="mb-10">
          <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-foreground">Evidence</h3>

          {!loadStage && brief.status === "ready" && (
            <div className="space-y-6">
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
              <p className="text-sm text-muted-foreground">Showing the underlying deterministic evidence instead.</p>
              {matchedEvidence ? (
                <div className="space-y-5">
                  <p className="font-mono text-xs text-muted-foreground">
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

          {/* decision-level gaps — evidence gaps that apply to the decision as
              a whole rather than to any single matched issue */}
          {evaluation && evaluation.decision_level_gaps.length > 0 && (
            <div className="mt-6">
              <FactList title="Decision-level gaps" facts={evaluation.decision_level_gaps} accent="text-muted-foreground" />
            </div>
          )}
        </div>

        {/* challenge / stability — grouped under one pipeline stage since
            both read the same underlying evaluation-to-evaluation history */}
        <div ref={challengeSectionRef}>
        {/* stability — Phase 4C: current evidence vs. changed evidence,
            distinct from the Challenge panel's full categorized event log.
            Presented as a two-point timeline (previous evaluation -> current)
            since that's exactly what compare_latest_two compares. */}
        {showStability && (
        <div className="mb-8 border-t border-border pt-6">
          <div className="mb-5 flex items-center justify-between">
            <h4 className="text-sm font-medium uppercase tracking-wide text-foreground">Stability</h4>
            <div className="flex items-center gap-3">
              {stability.status === "ready" && (
                <Badge variant="outline" className="rounded-full bg-surface-4 text-xs text-muted-foreground">
                  {stability.data.status.replace(/_/g, " ")}
                </Badge>
              )}
              <button onClick={refreshStability} className="text-xs text-muted-foreground hover:text-foreground">
                Refresh
              </button>
            </div>
          </div>

          {stability.status === "loading" && <p className="text-sm text-muted-foreground">Loading stability…</p>}
          {stability.status === "error" && (
            <p className="text-sm text-destructive">Couldn&rsquo;t load stability: {stability.message}</p>
          )}
          {stability.status === "ready" &&
            (stability.data.previous_evaluation_id === null ? (
              <p className="text-sm text-muted-foreground">
                Only one evaluation exists so far ({stability.data.evaluations_available}). Stability compares the two
                most recent evaluations for this decision — evaluate it again later to see what changed.
              </p>
            ) : (
              <div className="space-y-6">
                {/* two-point evaluation timeline */}
                <div className="flex items-center">
                  <TimelineNode label="Previous evaluation" time={formatRelativeTime(stability.data.previous_reference_at)} />
                  <span
                    className="mx-2 h-px flex-1"
                    style={{ background: "linear-gradient(90deg, var(--border), var(--brass))" }}
                    aria-hidden="true"
                  />
                  <TimelineNode
                    label="Current evaluation"
                    time={formatRelativeTime(stability.data.current_reference_at)}
                    active
                  />
                </div>
                {stability.data.evaluation_version_mismatch && (
                  <p className="text-xs text-warning">Matching-rule version changed between evaluations.</p>
                )}

                <div>
                  <p className="mb-2.5 text-xs font-medium text-muted-foreground">
                    Current vs. changed evidence — matched issues
                  </p>
                  <ul className="space-y-2">
                    {stability.data.issue_changes.length === 0 && (
                      <li className="text-sm text-muted-foreground">No issue-level changes.</li>
                    )}
                    {stability.data.issue_changes.map((change) => (
                      <li
                        key={change.product_issue_id}
                        className="flex items-center justify-between gap-3 border-b border-border py-2.5 text-sm"
                      >
                        <span className="text-foreground">
                          {(challengeState.status === "ready" &&
                            challengeState.data.issue_summaries[change.product_issue_id]?.title) ||
                            change.product_issue_id.slice(0, 8)}
                        </span>
                        <Badge
                          variant="outline"
                          className={`shrink-0 rounded-full text-xs uppercase ${STABILITY_STATUS_STYLE[change.status] ?? STABILITY_STATUS_STYLE.no_change}`}
                        >
                          {change.status.replace(/_/g, " ")}
                        </Badge>
                      </li>
                    ))}
                  </ul>
                </div>

                {stability.data.decision_level_events.length > 0 && (
                  <div>
                    <p className="mb-2.5 text-xs font-medium text-muted-foreground">Decision-level evidence gaps</p>
                    <ul className="space-y-2 text-sm text-muted-foreground">
                      {stability.data.decision_level_events.map((event, i) => (
                        <li key={i} className="border-l-2 border-border/60 pl-3 font-mono text-xs">
                          {event.fact_code ?? event.type} · {event.interpretation.replace(/_/g, " ")}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            ))}
        </div>
      )}

      {/* challenge panel — "audit mode": what changed since the last
          evaluation, grouped by category. Never a score, ranking, or
          recommendation, per the backend contract. */}
      {showChallenge && (
        <div className="mb-8 border-t border-border pt-6">
          <div className="mb-1 flex items-center justify-between">
            <h4 className="text-sm font-medium uppercase tracking-wide text-foreground">Challenge this decision</h4>
            <div className="flex items-center gap-3">
              {challengeState.status === "ready" && (
                <Badge variant="outline" className="rounded-full bg-surface-4 text-xs text-muted-foreground">
                  {challengeState.data.status.replace(/_/g, " ")}
                </Badge>
              )}
              <button onClick={refreshChallenge} className="text-xs text-muted-foreground hover:text-foreground">
                Refresh
              </button>
            </div>
          </div>
          <p className="mb-4 text-sm text-muted-foreground">What changed?</p>

          {challengeState.status === "loading" && <p className="text-sm text-muted-foreground">Loading challenge…</p>}
          {challengeState.status === "error" && (
            <p className="text-sm text-destructive">Couldn&rsquo;t load challenge: {challengeState.message}</p>
          )}
          {challengeState.status === "ready" &&
            (challengeState.data.previous_evaluation_id === null ? (
              <p className="text-sm text-muted-foreground">
                Only one evaluation exists so far. Evaluate this decision again later to challenge it.
              </p>
            ) : (
              <div className="space-y-4">
                <ChangeBucket
                  title="New issues"
                  events={challengeState.data.new_issues}
                  summaries={challengeState.data.issue_summaries}
                />
                <ChangeBucket
                  title="Disappeared issues"
                  events={challengeState.data.disappeared_issues}
                  summaries={challengeState.data.issue_summaries}
                />
                <ChangeBucket
                  title="Supporting changes"
                  events={challengeState.data.supporting_changes}
                  summaries={challengeState.data.issue_summaries}
                />
                <ChangeBucket
                  title="Counter changes"
                  events={challengeState.data.counter_changes}
                  summaries={challengeState.data.issue_summaries}
                />
                <ChangeBucket
                  title="Trend changes"
                  events={challengeState.data.trend_changes}
                  summaries={challengeState.data.issue_summaries}
                />
                <ChangeBucket
                  title="Gap changes"
                  events={challengeState.data.gap_changes}
                  summaries={challengeState.data.issue_summaries}
                />
                <ChangeBucket
                  title="Relevance changes"
                  events={challengeState.data.relevance_changes}
                  summaries={challengeState.data.issue_summaries}
                />
                <ChangeBucket
                  title="Value changes"
                  events={challengeState.data.value_changes}
                  summaries={challengeState.data.issue_summaries}
                />
                <ChangeBucket
                  title="Decision-level changes"
                  events={challengeState.data.decision_level_changes}
                  summaries={challengeState.data.issue_summaries}
                />
                {challengeState.data.all_events.length === 0 && (
                  <p className="text-sm text-muted-foreground">No evidence changes since the previous evaluation.</p>
                )}
              </div>
            ))}
        </div>
      )}
        </div>

      {/* raw sources */}
      {showSources && (
        <div className="mb-8 border-t border-border pt-6">
          <h4 className="mb-3 text-sm font-medium uppercase tracking-wide text-foreground">Raw source records</h4>
          {sourcesLoading && <p className="text-sm text-muted-foreground">Loading raw feedback…</p>}
          {sourcesError && <p className="text-sm text-destructive">{sourcesError}</p>}
          {sources && (
            <ul className="text-sm text-muted-foreground">
              {sources.length === 0 ? (
                <li>No individual records are linked to this cluster yet.</li>
              ) : (
                sources.map((source) => (
                  <li key={source.id} className="border-b border-border py-3">
                    <div className="mb-1.5 flex items-center gap-2 text-xs">
                      <span className="font-mono text-muted-foreground">{source.id.slice(0, 8)}</span>
                      <span className="uppercase tracking-wide text-brass">
                        {sourceTypeLabel(source.source_type)} · {source.source}
                      </span>
                    </div>
                    <p className="leading-relaxed text-foreground">&ldquo;{source.raw_text}&rdquo;</p>
                  </li>
                ))
              )}
            </ul>
          )}
        </div>
      )}

      {/* actions */}
      <div className="mt-auto flex flex-wrap gap-3 border-t border-border pt-6">
        <Button
          onClick={() => setShowStability((v) => !v)}
          variant="outline"
          disabled={!decisionId}
          className="min-w-[140px] flex-1"
          size="lg"
        >
          {showStability ? "Hide stability" : "View stability"}
        </Button>
        <Button onClick={() => setShowChallenge((v) => !v)} disabled={!decisionId} className="min-w-[140px] flex-1" size="lg">
          {showChallenge ? "Hide challenge" : "Challenge this decision"}
        </Button>
        <Button onClick={toggleSources} variant="outline" className="min-w-[140px] flex-1" size="lg">
          {showSources ? "Hide raw sources" : "View raw sources"}
        </Button>
      </div>
      </div>
    </div>
  );
}

const STABILITY_STATUS_STYLE: Record<string, string> = {
  strengthened: "border-success/30 bg-success/10 text-success",
  new: "border-chart-2/30 bg-chart-2/10 text-chart-2",
  weakened: "border-warning/30 bg-warning/10 text-warning",
  removed: "border-destructive/30 bg-destructive/10 text-destructive",
  mixed: "border-border bg-surface-4 text-muted-foreground",
  no_change: "border-border bg-surface-4 text-muted-foreground",
};

function TimelineNode({ label, time, active }: { label: string; time: string; active?: boolean }) {
  return (
    <div className="flex shrink-0 items-center gap-2.5">
      <span
        className={`h-2.5 w-2.5 rounded-full ${active ? "bg-brass" : "bg-muted-foreground/40"}`}
        aria-hidden="true"
      />
      <div>
        <p className={`text-sm font-medium ${active ? "text-foreground" : "text-muted-foreground"}`}>{label}</p>
        <p className="font-mono text-xs text-muted-foreground">{time}</p>
      </div>
    </div>
  );
}

function SignalTile({
  label,
  value,
  accent,
}: {
  label: string;
  value: string | number;
  accent?: string;
}) {
  return (
    <div className="border-l border-border pl-3">
      <p className={`font-mono text-lg font-medium ${accent ?? "text-foreground"}`}>{value}</p>
      <p className="mt-0.5 text-xs text-muted-foreground">{label}</p>
    </div>
  );
}

const EVIDENCE_CARD_BORDER: Record<string, string> = {
  "text-chart-2": "border-l-chart-2",
  "text-chart-1": "border-l-warning",
  "text-muted-foreground": "border-l-border",
};

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
    <div className={`border-l-2 bg-surface-2 p-4 ${EVIDENCE_CARD_BORDER[accent] ?? "border-l-border"}`}>
      <p className={`mb-2.5 text-xs font-semibold uppercase tracking-wide ${accent}`}>{title}</p>
      <ul className="space-y-3">
        {claims.map((claim, i) => (
          <li key={i} className="leading-relaxed text-muted-foreground">
            {claim.text}{" "}
            {claim.citation_ids.map((id) => (
              <button
                key={id}
                onClick={() => onToggleCitation(expandedCitation === id ? null : id)}
                className="ml-1 rounded-full bg-surface-4 px-1.5 py-0.5 font-mono text-xs text-foreground hover:bg-surface-4/70"
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
                    className="mt-2 rounded-md border border-border/60 bg-background/60 p-3 font-mono text-xs text-foreground"
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
    <div className={`border-l-2 bg-surface-2 p-4 ${EVIDENCE_CARD_BORDER[accent] ?? "border-l-border"}`}>
      <p className={`mb-2.5 text-xs font-semibold uppercase tracking-wide ${accent}`}>{title}</p>
      <ul className="space-y-2 text-sm text-muted-foreground">
        {facts.map((fact) => (
          <li key={fact.code}>
            <span className="mr-2 font-mono text-foreground">{fact.label}</span>
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
    <div className="border-l-2 border-border pl-4">
      <p className="mb-2.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">{title}</p>
      <ul className="space-y-2">
        {events.map((event, i) => {
          const issueTitle = event.product_issue_id ? summaries[event.product_issue_id]?.title : null;
          return (
            <li key={i} className="flex flex-wrap items-baseline gap-x-2 text-sm">
              {issueTitle && <span className="font-medium text-foreground">{issueTitle}</span>}
              <span className="font-mono text-xs text-muted-foreground">
                {event.fact_code ?? event.type} · {event.interpretation.replace(/_/g, " ")}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

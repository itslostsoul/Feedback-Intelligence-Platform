"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  api,
  describeApiError,
  type FeedbackAnalysisRead,
  type IssueDiscoveryResponse,
  type ProductIssueDetail,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { categoryBadgeClass, categoryLabel } from "@/lib/format";
import { Check, X } from "lucide-react";

// Orchestrates the existing backend pipeline for one already-created feedback
// item — never re-ingests it. Every step below is an existing, documented
// endpoint (see docs/frontend-integration.md §4-6); this component only
// sequences them and reflects their real results. Each step is safe to
// re-run (analyze/embed/semantic-analysis upsert, discover and signal
// recalculation overwrite rather than duplicate), so "Retry" after a failure
// is just calling the same function again.
type StepKey = "analyze" | "embed" | "semantic" | "discover" | "signals";
type StepStatus = "pending" | "running" | "done" | "error" | "skipped";

interface StepState {
  status: StepStatus;
  message?: string;
}

const STEP_ORDER: { key: StepKey; label: string }[] = [
  { key: "analyze", label: "Analyzing" },
  { key: "embed", label: "Embedding" },
  { key: "semantic", label: "Finding relationships" },
  { key: "discover", label: "Updating product issues" },
  { key: "signals", label: "Updating signals" },
];

function initialSteps(): Record<StepKey, StepState> {
  return {
    analyze: { status: "pending" },
    embed: { status: "pending" },
    semantic: { status: "pending" },
    discover: { status: "pending" },
    signals: { status: "pending" },
  };
}

interface ProcessResult {
  analysis: FeedbackAnalysisRead | null;
  issue: ProductIssueDetail | null;
  discovery: IssueDiscoveryResponse;
}

export default function ProcessFeedbackPanel({
  feedbackId,
  onClose,
  onComplete,
}: {
  feedbackId: string | null;
  onClose: () => void;
  onComplete: () => void;
}) {
  const [overall, setOverall] = useState<"idle" | "running" | "complete" | "error">("idle");
  const [steps, setSteps] = useState<Record<StepKey, StepState>>(initialSteps());
  const [result, setResult] = useState<ProcessResult | null>(null);
  const activeIdRef = useRef<string | null>(null);

  useEffect(() => {
    // Nested + immediately invoked so no setState call executes directly in
    // the effect body — same convention used in DecisionBrief.tsx.
    function reset() {
      activeIdRef.current = feedbackId;
      setOverall("idle");
      setSteps(initialSteps());
      setResult(null);
    }
    reset();
  }, [feedbackId]);

  if (!feedbackId) return null;
  const currentId = feedbackId;

  function updateStep(key: StepKey, patch: Partial<StepState>) {
    setSteps((prev) => ({ ...prev, [key]: { ...prev[key], ...patch } }));
  }

  async function runPipeline() {
    const stillActive = () => activeIdRef.current === currentId;
    setOverall("running");
    setSteps(initialSteps());
    setResult(null);

    updateStep("analyze", { status: "running" });
    try {
      await api.feedback.analyze(currentId);
      if (!stillActive()) return;
      updateStep("analyze", { status: "done" });
    } catch (err) {
      if (stillActive()) {
        updateStep("analyze", { status: "error", message: describeApiError(err) });
        setOverall("error");
      }
      return;
    }

    updateStep("embed", { status: "running" });
    try {
      await api.feedback.embed(currentId);
      if (!stillActive()) return;
      updateStep("embed", { status: "done" });
    } catch (err) {
      if (stillActive()) {
        updateStep("embed", { status: "error", message: describeApiError(err) });
        setOverall("error");
      }
      return;
    }

    updateStep("semantic", { status: "running" });
    try {
      await api.feedback.semanticAnalysis(currentId);
      if (!stillActive()) return;
      updateStep("semantic", { status: "done" });
    } catch (err) {
      if (stillActive()) {
        updateStep("semantic", { status: "error", message: describeApiError(err) });
        setOverall("error");
      }
      return;
    }

    updateStep("discover", { status: "running" });
    let discovery: IssueDiscoveryResponse;
    try {
      discovery = await api.issues.discover();
      if (!stillActive()) return;
      updateStep("discover", { status: "done" });
    } catch (err) {
      if (stillActive()) {
        updateStep("discover", { status: "error", message: describeApiError(err) });
        setOverall("error");
      }
      return;
    }

    // There's no "look up an issue by feedback id" endpoint, so finding
    // which (if any) product issue this feedback now belongs to means
    // checking membership across the current issue list — fine at
    // hackathon-demo scale (a handful of issues), and still a real read
    // against real data, never guessed.
    let associatedIssue: ProductIssueDetail | null = null;
    try {
      const page = await api.issues.list({ limit: 100 });
      const details = await Promise.all(page.items.map((i) => api.issues.get(i.id)));
      associatedIssue = details.find((d) => d.feedback_ids.includes(currentId)) ?? null;
    } catch {
      // Non-fatal — this lookup is a display nicety, not a pipeline step.
    }
    if (!stillActive()) return;

    if (associatedIssue) {
      updateStep("signals", { status: "running" });
      try {
        await api.issues.recalculateSignals(associatedIssue.id);
        if (!stillActive()) return;
        updateStep("signals", { status: "done" });
      } catch (err) {
        if (stillActive()) {
          updateStep("signals", { status: "error", message: describeApiError(err) });
          setOverall("error");
        }
        return;
      }
    } else {
      updateStep("signals", {
        status: "skipped",
        message: "Not yet linked to a product issue — needs another similar report to form a pattern.",
      });
    }

    const analysis = await api.feedback.getAnalysis(currentId).catch(() => null);
    if (!stillActive()) return;
    setResult({ analysis, issue: associatedIssue, discovery });
    setOverall("complete");
    onComplete();
  }

  return (
    <Sheet open={!!feedbackId} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <SheetContent side="right" className="w-full border-none p-0 sm:max-w-xl">
        <div className="flex h-screen w-full flex-col overflow-y-auto bg-surface-3 text-foreground">
          <div className="border-b border-border px-8 py-6 md:px-10">
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-brass">Processing signal</p>
            <h2 className="mb-1 font-serif text-2xl tracking-tight">Process feedback</h2>
            <p className="font-mono text-xs text-muted-foreground">{currentId.slice(0, 8)}</p>
          </div>

          <div className="flex flex-1 flex-col p-8 md:p-10">
          <ol className="mb-8">
            <StepRow label="Feedback received" status="done" index={1} />
            {STEP_ORDER.map(({ key, label }, i) => (
              <StepRow
                key={key}
                label={label}
                status={steps[key].status}
                message={steps[key].message}
                index={i + 2}
              />
            ))}
            <StepRow
              label="Complete"
              status={overall === "complete" ? "done" : "pending"}
              index={STEP_ORDER.length + 2}
              last
            />
          </ol>

          {overall === "error" && (
            <div className="mb-6 border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
              Processing stopped at the failed step above. Nothing already completed was undone, and every step is
              safe to retry — it won&rsquo;t create duplicate feedback or duplicate data.
            </div>
          )}

          {overall === "complete" && result && (
            <div className="mb-8 space-y-4 border-t border-border pt-5">
              <p className="text-xs font-medium uppercase tracking-wide text-success">Result</p>
              {result.analysis ? (
                <div className="flex flex-wrap items-center gap-2">
                  <Badge
                    variant="outline"
                    className={`rounded-sm font-medium ${categoryBadgeClass(result.analysis.category)}`}
                  >
                    {categoryLabel(result.analysis.category)}
                  </Badge>
                  <span className="font-mono text-xs text-muted-foreground">
                    severity {result.analysis.severity} · confidence {(result.analysis.confidence * 100).toFixed(0)}%
                  </span>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">Analysis result unavailable.</p>
              )}

              {result.issue ? (
                <div className="text-sm">
                  <p className="text-muted-foreground">Linked to product issue:</p>
                  <p className="font-medium text-foreground">{result.issue.title}</p>
                  <Link href={`/?issue=${result.issue.id}`} className="text-xs text-brass underline">
                    View in Overview →
                  </Link>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">
                  Not yet linked to a product issue — needs at least one more similar report to form a pattern.
                </p>
              )}

              <p className="font-mono text-xs text-muted-foreground">
                Discovery: {result.discovery.issues_created} created · {result.discovery.issues_updated} updated ·{" "}
                {result.discovery.feedback_linked} feedback linked
              </p>
            </div>
          )}

          <div className="mt-auto flex gap-3 border-t border-border pt-8">
            <Button onClick={runPipeline} disabled={overall === "running"} className="flex-1" size="lg">
              {overall === "running"
                ? "Processing…"
                : overall === "error"
                  ? "Retry"
                  : overall === "complete"
                    ? "Re-run pipeline"
                    : "Process feedback"}
            </Button>
            <Button onClick={onClose} variant="outline" className="flex-1" size="lg">
              Close
            </Button>
          </div>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}

const STEP_ROW_TEXT: Record<StepStatus, string> = {
  pending: "text-muted-foreground",
  running: "text-brass",
  done: "text-foreground",
  error: "text-destructive",
  skipped: "text-muted-foreground",
};

/** A plain numbered sequence — "01 Feedback received ✓" — rather than icons
 * in circles connected by a line. The mark on the right (✓ / ✕ / ●) is the
 * only technical/mono element; everything else reads as prose. */
function StepRow({
  label,
  status,
  message,
  index,
  last,
}: {
  label: string;
  status: StepStatus;
  message?: string;
  index: number;
  last?: boolean;
}) {
  return (
    <li className={`flex items-start gap-4 py-3 ${last ? "" : "border-b border-border"}`}>
      <span className="w-6 shrink-0 pt-0.5 font-mono text-xs tabular-nums text-muted-foreground">
        {String(index).padStart(2, "0")}
      </span>
      <div className="flex-1">
        <p className={`text-sm ${status === "pending" ? "text-muted-foreground" : "font-medium text-foreground"}`}>
          {label}
        </p>
        {message && <p className="mt-0.5 text-xs text-muted-foreground">{message}</p>}
      </div>
      <span className={`pt-0.5 text-sm ${STEP_ROW_TEXT[status]}`}>
        {status === "done" ? (
          <Check className="h-4 w-4" strokeWidth={2.5} />
        ) : status === "error" ? (
          <X className="h-4 w-4" strokeWidth={2.5} />
        ) : status === "running" ? (
          "●"
        ) : (
          "○"
        )}
      </span>
    </li>
  );
}

"use client";

import { useEffect, useState } from "react";
import {
  api,
  describeApiError,
  type FeedbackCreate,
  type FeedbackRead,
  type ProcessingStatus,
  type SourceType,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/PageHeader";
import ProcessFeedbackPanel from "@/components/ui/ProcessFeedbackPanel";
import { formatRelativeTime, processingStatusLabel, sourceTypeLabel } from "@/lib/format";

const PAGE_SIZE = 25;

const SOURCE_TYPES: SourceType[] = [
  "review",
  "support_ticket",
  "survey",
  "social",
  "email",
  "interview",
  "other",
];

const STATUS_VALUES: ProcessingStatus[] = [
  "received",
  "pending_analysis",
  "analyzing",
  "analyzed",
  "needs_review",
  "failed",
];

function statusTextClass(status: ProcessingStatus): string {
  switch (status) {
    case "analyzed":
      return "text-chart-2";
    case "analyzing":
    case "pending_analysis":
      return "text-chart-2";
    case "needs_review":
      return "text-warning";
    case "failed":
      return "text-destructive";
    default:
      return "text-muted-foreground";
  }
}

const selectClass =
  "border-b border-border bg-transparent px-0 py-1 text-xs text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:text-foreground";
const filterInputClass =
  "w-40 border-b border-border bg-transparent px-0 py-1 text-xs text-foreground outline-none transition-colors placeholder:text-muted-foreground/60 focus-visible:border-brass";
const inputClass =
  "w-full border-b border-border bg-transparent px-0 py-2 text-sm text-foreground outline-none transition-colors focus-visible:border-brass";

export default function FirehosePage() {
  const [items, setItems] = useState<FeedbackRead[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [sourceTypeFilter, setSourceTypeFilter] = useState<SourceType | "">("");
  const [statusFilter, setStatusFilter] = useState<ProcessingStatus | "">("");
  const [sourceQuery, setSourceQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [refreshToken, setRefreshToken] = useState(0);
  const [processingId, setProcessingId] = useState<string | null>(null);
  const [showIngestForm, setShowIngestForm] = useState(false);

  // Debounce the free-text source filter so every keystroke doesn't fire a
  // request — mirrors the same "reset from offset 0" effect below once the
  // committed value actually changes.
  useEffect(() => {
    const handle = setTimeout(() => setSourceFilter(sourceQuery.trim()), 350);
    return () => clearTimeout(handle);
  }, [sourceQuery]);

  // Filter changes (and a manual refresh, via refreshToken) reset the list
  // from offset 0. The fetch lives inside the effect as an immediately
  // invoked nested function, per the canonical data-fetching-effect pattern.
  useEffect(() => {
    let cancelled = false;

    async function loadFirstPage() {
      setIsLoading(true);
      setError(null);
      try {
        const page = await api.feedback.list({
          limit: PAGE_SIZE,
          offset: 0,
          source: sourceFilter || undefined,
          source_type: sourceTypeFilter || undefined,
          status: statusFilter || undefined,
        });
        if (cancelled) return;
        setItems(page.items);
        setOffset(page.items.length);
        setTotal(page.total);
      } catch (err) {
        if (!cancelled) setError(describeApiError(err));
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    loadFirstPage();
    return () => {
      cancelled = true;
    };
  }, [sourceTypeFilter, statusFilter, sourceFilter, refreshToken]);

  async function loadMore() {
    setIsLoadingMore(true);
    try {
      const page = await api.feedback.list({
        limit: PAGE_SIZE,
        offset,
        source: sourceFilter || undefined,
        source_type: sourceTypeFilter || undefined,
        status: statusFilter || undefined,
      });
      setItems((prev) => [...prev, ...page.items]);
      setOffset(offset + page.items.length);
      setTotal(page.total);
    } catch (err) {
      setError(describeApiError(err));
    } finally {
      setIsLoadingMore(false);
    }
  }

  const hasActiveFilters = sourceTypeFilter !== "" || statusFilter !== "" || sourceQuery !== "";

  return (
    <main className="mx-auto max-w-[1180px] px-6 py-12 md:px-12 md:py-16">
      <PageHeader
        eyebrow="SignalForge"
        title="Raw Firehose"
        description="Every signal, unfiltered — before clustering, before signals, before a decision is ever asked."
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => setShowIngestForm((v) => !v)}>
              {showIngestForm ? "Hide ingest form" : "Ingest feedback"}
            </Button>
          </>
        }
      />

      {showIngestForm && (
        <div className="mb-6 space-y-6">
          <IngestForm
            onIngested={() => setRefreshToken((t) => t + 1)}
            onRequestProcess={(id) => setProcessingId(id)}
          />
        </div>
      )}
      <PipelineTools />

      {/* FILTER BAR — source is a real, already-supported query param that
          simply wasn't exposed in the UI yet */}
      <div className="mt-8 mb-8 flex flex-wrap items-center gap-6 border-b border-border pb-4">
        <input
          className={filterInputClass}
          value={sourceQuery}
          onChange={(e) => setSourceQuery(e.target.value)}
          placeholder="Filter by source…"
        />
        <select
          className={selectClass}
          value={sourceTypeFilter}
          onChange={(e) => setSourceTypeFilter(e.target.value as SourceType | "")}
        >
          <option value="">All source types</option>
          {SOURCE_TYPES.map((type) => (
            <option key={type} value={type}>
              {sourceTypeLabel(type)}
            </option>
          ))}
        </select>
        <select
          className={selectClass}
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as ProcessingStatus | "")}
        >
          <option value="">All statuses</option>
          {STATUS_VALUES.map((status) => (
            <option key={status} value={status}>
              {processingStatusLabel(status)}
            </option>
          ))}
        </select>
        {hasActiveFilters && (
          <button
            onClick={() => {
              setSourceQuery("");
              setSourceTypeFilter("");
              setStatusFilter("");
            }}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            Clear
          </button>
        )}
        <span className="ml-auto font-mono text-xs text-muted-foreground">{total.toLocaleString()} total</span>
      </div>

      {error && (
        <div className="mb-6 border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          Couldn&rsquo;t reach the backend: {error}
        </div>
      )}

      <div className="mb-6 flex items-baseline gap-3 border-b border-border pb-3">
        <span className="relative flex h-1.5 w-1.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-chart-2 opacity-50" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-chart-2" />
        </span>
        <h2 className="text-sm font-medium uppercase tracking-wide text-foreground">Live signals</h2>
      </div>

      {/* ACTIVITY STREAM — numbered editorial rows, thin rules, not cards */}
      <div>
        {isLoading
          ? [...Array(6)].map((_, i) => (
              <div key={i} className="border-b border-border py-5">
                <div className="h-3 w-32 animate-pulse bg-foreground/[0.05]" />
                <div className="mt-3 h-4 w-3/4 animate-pulse bg-foreground/[0.05]" />
              </div>
            ))
          : items.length === 0 && !error
            ? (
                <div className="border-b border-border py-12 text-center text-sm text-muted-foreground">
                  No feedback matches these filters yet.
                </div>
              )
            : items.map((item, i) => (
                <div key={item.id} className="flex items-start gap-5 border-b border-border py-5">
                  <span className="w-7 shrink-0 pt-0.5 font-mono text-xs tabular-nums text-muted-foreground/50">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="mb-1.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      <span className="uppercase tracking-wide">{sourceTypeLabel(item.source_type)}</span>
                      <span className="text-border">·</span>
                      <span>{item.source}</span>
                      <span className="text-border">·</span>
                      <span>{formatRelativeTime(item.ingested_at)}</span>
                    </div>
                    <p className="max-w-[68ch] text-[15px] leading-snug text-foreground">
                      &ldquo;{item.raw_text}&rdquo;
                    </p>
                    <p className={`mt-1.5 text-xs font-medium uppercase tracking-wide ${statusTextClass(item.processing_status)}`}>
                      {processingStatusLabel(item.processing_status)}
                    </p>
                  </div>
                  {(item.processing_status === "received" || item.processing_status === "failed") && (
                    <Button size="sm" variant="outline" onClick={() => setProcessingId(item.id)} className="shrink-0">
                      {item.processing_status === "failed" ? "Retry" : "Process"}
                    </Button>
                  )}
                </div>
              ))}
      </div>

      {!isLoading && items.length < total && (
        <div className="mt-6 text-center">
          <button
            onClick={loadMore}
            disabled={isLoadingMore}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            {isLoadingMore ? "Loading…" : `Load more (${total - items.length} remaining)`}
          </button>
        </div>
      )}

      <ProcessFeedbackPanel
        feedbackId={processingId}
        onClose={() => setProcessingId(null)}
        onComplete={() => setRefreshToken((t) => t + 1)}
      />
    </main>
  );
}

function PipelineTools() {
  const [discovering, setDiscovering] = useState(false);
  const [recalculating, setRecalculating] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleDiscover() {
    setDiscovering(true);
    setError(null);
    try {
      const result = await api.issues.discover();
      setMessage(
        `Discovery ran: ${result.issues_created} issue(s) created, ${result.issues_updated} updated, ${result.feedback_linked} feedback item(s) linked.`
      );
    } catch (err) {
      setError(describeApiError(err));
    } finally {
      setDiscovering(false);
    }
  }

  async function handleRecalculate() {
    setRecalculating(true);
    setError(null);
    try {
      const result = await api.issues.recalculateAllSignals();
      setMessage(`Signals recalculated for ${result.issues_processed} issue(s).`);
    } catch (err) {
      setError(describeApiError(err));
    } finally {
      setRecalculating(false);
    }
  }

  return (
    <div className="border border-border bg-surface-2 p-5">
      <p className="mb-3 text-sm font-medium text-foreground">Pipeline tools</p>
      <p className="mb-4 text-sm leading-relaxed text-muted-foreground">
        Ingesting feedback only creates a <span className="font-mono text-xs">received</span> row. Use{" "}
        <span className="font-mono text-xs">Process</span> next to a row below to run analysis, embedding,
        relationship discovery, issue discovery and signal recalculation in one go. These remain useful for
        bulk/global re-runs:
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="outline" size="sm" onClick={handleDiscover} disabled={discovering}>
          {discovering ? "Discovering…" : "Run issue discovery"}
        </Button>
        <Button variant="outline" size="sm" onClick={handleRecalculate} disabled={recalculating}>
          {recalculating ? "Recalculating…" : "Recalculate all signals"}
        </Button>
      </div>
      {message && <p className="mt-3 font-mono text-xs text-chart-2">{message}</p>}
      {error && <p className="mt-3 text-xs text-destructive">{error}</p>}
    </div>
  );
}

function IngestForm({
  onIngested,
  onRequestProcess,
}: {
  onIngested: () => void;
  onRequestProcess: (id: string) => void;
}) {
  const [source, setSource] = useState("");
  const [sourceType, setSourceType] = useState<SourceType>("other");
  const [rawText, setRawText] = useState("");
  const [customerId, setCustomerId] = useState("");
  const [customerTier, setCustomerTier] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{ kind: "created" | "duplicate"; id: string } | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setFormError(null);
    setResult(null);
    try {
      const payload: FeedbackCreate = {
        source: source.trim(),
        source_type: sourceType,
        raw_text: rawText.trim(),
        customer_id: customerId.trim() || null,
        customer_tier: customerTier.trim() || null,
      };
      const created = await api.feedback.create(payload);
      // A 200 (vs 201) means the backend matched an existing (source, external_id)
      // pair — we don't send external_id here, so this is effectively always a
      // fresh row, but we still read processing_status/id straight from the response.
      setResult({ kind: "created", id: created.id });
      setRawText("");
      onIngested();
    } catch (err) {
      setFormError(describeApiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="border border-border bg-surface-2 p-5">
      <p className="mb-4 text-sm font-medium text-foreground">Ingest feedback</p>
      <form onSubmit={handleSubmit} className="grid gap-4 md:grid-cols-2">
        <div>
          <label className="mb-1.5 block text-xs font-medium text-muted-foreground">
            Source <span className="text-warning">*</span>
          </label>
          <input
            className={inputClass}
            value={source}
            onChange={(e) => setSource(e.target.value)}
            placeholder="app_store, zendesk, sales_call…"
            required
          />
        </div>
        <div>
          <label className="mb-1.5 block text-xs font-medium text-muted-foreground">Source type</label>
          <select
            className={selectClass + " h-9 w-full text-sm"}
            value={sourceType}
            onChange={(e) => setSourceType(e.target.value as SourceType)}
          >
            {SOURCE_TYPES.map((type) => (
              <option key={type} value={type}>
                {sourceTypeLabel(type)}
              </option>
            ))}
          </select>
        </div>
        <div className="md:col-span-2">
          <label className="mb-1.5 block text-xs font-medium text-muted-foreground">
            Feedback text <span className="text-warning">*</span>
          </label>
          <textarea
            className={inputClass}
            rows={3}
            value={rawText}
            onChange={(e) => setRawText(e.target.value)}
            placeholder="Checkout fails every time I try to pay."
            required
          />
        </div>
        <div>
          <label className="mb-1.5 block text-xs font-medium text-muted-foreground">Customer ID (optional)</label>
          <input className={inputClass} value={customerId} onChange={(e) => setCustomerId(e.target.value)} />
        </div>
        <div>
          <label className="mb-1.5 block text-xs font-medium text-muted-foreground">Customer tier (optional)</label>
          <input className={inputClass} value={customerTier} onChange={(e) => setCustomerTier(e.target.value)} />
        </div>
        <div className="md:col-span-2 flex items-center gap-3">
          <Button type="submit" disabled={submitting}>
            {submitting ? "Ingesting…" : "Ingest feedback"}
          </Button>
          {result && (
            <span className="flex items-center gap-2">
              <span className="font-mono text-xs text-chart-2">Stored as {result.id.slice(0, 8)}</span>
              <Button type="button" size="sm" variant="outline" onClick={() => onRequestProcess(result.id)}>
                Process feedback
              </Button>
            </span>
          )}
          {formError && <span className="text-xs text-destructive">{formError}</span>}
        </div>
      </form>
    </div>
  );
}

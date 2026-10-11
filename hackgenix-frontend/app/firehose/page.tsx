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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";
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

function statusBadgeClass(status: ProcessingStatus): string {
  switch (status) {
    case "analyzed":
      return "bg-chart-2/15 text-chart-2 border-chart-2/25";
    case "needs_review":
      return "bg-chart-5/15 text-chart-5 border-chart-5/25";
    case "failed":
      return "bg-destructive/15 text-destructive border-destructive/25";
    default:
      return "bg-muted text-muted-foreground border-border";
  }
}

const selectClass =
  "h-8 rounded-lg border border-border bg-background px-2 text-sm text-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30";
const inputClass =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30";

export default function FirehosePage() {
  const [items, setItems] = useState<FeedbackRead[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [sourceTypeFilter, setSourceTypeFilter] = useState<SourceType | "">("");
  const [statusFilter, setStatusFilter] = useState<ProcessingStatus | "">("");
  const [refreshToken, setRefreshToken] = useState(0);

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
  }, [sourceTypeFilter, statusFilter, refreshToken]);

  async function loadMore() {
    setIsLoadingMore(true);
    try {
      const page = await api.feedback.list({
        limit: PAGE_SIZE,
        offset,
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

  return (
    <main className="mx-auto max-w-[1180px] px-6 py-10 md:px-12">
      <div className="mb-8">
        <p className="mb-2 font-mono text-xs uppercase tracking-wider text-muted-foreground">
          Raw feedback firehose
        </p>
        <h1 className="mb-2 text-3xl font-semibold tracking-tight text-foreground md:text-4xl">
          Every signal, unfiltered
        </h1>
        <p className="max-w-[52ch] text-sm text-muted-foreground">
          Individual feedback items as they&rsquo;re ingested — before clustering, before signals, before a
          decision is ever asked.
        </p>
      </div>

      <IngestForm onIngested={() => setRefreshToken((t) => t + 1)} />
      <PipelineTools />

      <div className="mt-8 mb-4 flex flex-wrap items-center gap-3">
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
        <span className="font-mono text-xs text-muted-foreground">{total.toLocaleString()} total</span>
      </div>

      {error && (
        <div className="mb-6 rounded-lg border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
          Couldn&rsquo;t reach the backend: {error}
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-border bg-card">
        <Table>
          <TableHeader>
            <TableRow className="border-border hover:bg-transparent">
              <TableHead className="h-11 pl-8 font-mono text-[11px] uppercase tracking-wider text-muted-foreground md:pl-10">
                Feedback
              </TableHead>
              <TableHead className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
                Source
              </TableHead>
              <TableHead className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
                Status
              </TableHead>
              <TableHead className="pr-8 text-right font-mono text-[11px] uppercase tracking-wider text-muted-foreground md:pr-10">
                Ingested
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading
              ? [...Array(6)].map((_, i) => (
                  <TableRow key={i} className="border-border hover:bg-transparent">
                    <TableCell className="py-5 pl-8 md:pl-10">
                      <div className="h-4 w-3/4 animate-pulse rounded bg-muted" />
                    </TableCell>
                    <TableCell>
                      <div className="h-5 w-20 animate-pulse rounded-sm bg-muted" />
                    </TableCell>
                    <TableCell>
                      <div className="h-5 w-16 animate-pulse rounded-sm bg-muted" />
                    </TableCell>
                    <TableCell className="py-5 pr-8 md:pr-10">
                      <div className="ml-auto h-4 w-12 animate-pulse rounded bg-muted" />
                    </TableCell>
                  </TableRow>
                ))
              : items.length === 0 && !error
                ? (
                    <TableRow className="border-border hover:bg-transparent">
                      <TableCell colSpan={4} className="py-10 text-center text-sm text-muted-foreground">
                        No feedback matches these filters yet.
                      </TableCell>
                    </TableRow>
                  )
                : items.map((item) => (
                    <TableRow key={item.id} className="border-border hover:bg-transparent">
                      <TableCell className="max-w-[420px] truncate py-5 pl-8 text-foreground md:pl-10">
                        {item.raw_text}
                      </TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground">
                        {sourceTypeLabel(item.source_type)} · {item.source}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className={`rounded-sm text-xs font-medium ${statusBadgeClass(item.processing_status)}`}>
                          {processingStatusLabel(item.processing_status)}
                        </Badge>
                      </TableCell>
                      <TableCell className="py-5 pr-8 text-right font-mono text-xs text-muted-foreground md:pr-10">
                        {formatRelativeTime(item.ingested_at)}
                      </TableCell>
                    </TableRow>
                  ))}
          </TableBody>
        </Table>

        {!isLoading && items.length < total && (
          <div className="border-t border-border bg-muted/40 p-4 text-center">
            <button
              onClick={loadMore}
              disabled={isLoadingMore}
              className="text-xs font-medium tracking-wide text-muted-foreground hover:text-foreground"
            >
              {isLoadingMore ? "Loading…" : `Load more (${total - items.length} remaining)`}
            </button>
          </div>
        )}
      </div>
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
    <Card className="mt-6">
      <CardHeader>
        <CardTitle>Pipeline tools</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="mb-4 text-sm text-muted-foreground">
          Ingesting feedback here only creates <span className="font-mono">received</span> rows. Turning that
          into Product Issues on the Impact Grid needs analysis, embedding and semantic-relationship steps
          (which require <span className="font-mono">GEMINI_API_KEY</span> on the backend) before discovery —
          or run <span className="font-mono">python scripts/seed_demo_data.py</span> for a ready-made demo
          dataset. Once relationships exist, these two steps are deterministic and safe to re-run any time:
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
      </CardContent>
    </Card>
  );
}

function IngestForm({ onIngested }: { onIngested: () => void }) {
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
    <Card>
      <CardHeader>
        <CardTitle>Ingest feedback</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="grid gap-3 md:grid-cols-2">
          <div>
            <label className="mb-1 block text-[11px] uppercase tracking-wider text-muted-foreground">
              Source <span className="text-chart-1">*</span>
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
            <label className="mb-1 block text-[11px] uppercase tracking-wider text-muted-foreground">
              Source type
            </label>
            <select
              className={selectClass + " w-full"}
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
            <label className="mb-1 block text-[11px] uppercase tracking-wider text-muted-foreground">
              Feedback text <span className="text-chart-1">*</span>
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
            <label className="mb-1 block text-[11px] uppercase tracking-wider text-muted-foreground">
              Customer ID (optional)
            </label>
            <input className={inputClass} value={customerId} onChange={(e) => setCustomerId(e.target.value)} />
          </div>
          <div>
            <label className="mb-1 block text-[11px] uppercase tracking-wider text-muted-foreground">
              Customer tier (optional)
            </label>
            <input className={inputClass} value={customerTier} onChange={(e) => setCustomerTier(e.target.value)} />
          </div>
          <div className="md:col-span-2 flex items-center gap-3">
            <Button type="submit" disabled={submitting}>
              {submitting ? "Ingesting…" : "Ingest feedback"}
            </Button>
            {result && (
              <span className="font-mono text-xs text-chart-2">Stored as {result.id.slice(0, 8)}</span>
            )}
            {formError && <span className="text-xs text-destructive">{formError}</span>}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

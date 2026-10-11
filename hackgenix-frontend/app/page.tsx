"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  api,
  describeApiError,
  type AnalyticsOverview,
  type AnalyticsTrendsResponse,
  type FeedbackCategory,
  type ProductIssueRead,
  type ProductIssueStatus,
  type TopIssueItem,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { IssueRow, type IssueRowData } from "@/components/ui/IssueRow";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import DecisionBrief from "@/components/ui/DecisionBrief";
import { IssueVolumeChart, CategoryDistribution, TrendChart } from "@/components/ui/DashboardCharts";
import { categoryLabel, formatRelativeTime, issueStatusLabel } from "@/lib/format";

const PAGE_SIZE = 20;

const ISSUE_STATUSES: ProductIssueStatus[] = ["open", "resolved", "dismissed"];
const ISSUE_CATEGORIES: FeedbackCategory[] = ["bug", "feature_request", "ui_friction", "performance", "billing"];

const selectClass =
  "border-b border-border bg-transparent px-0 py-1 text-xs text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:text-foreground";

export default function Dashboard() {
  const router = useRouter();
  const [issues, setIssues] = useState<ProductIssueRead[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [trends, setTrends] = useState<AnalyticsTrendsResponse | null>(null);
  const [signalById, setSignalById] = useState<Map<string, TopIssueItem>>(new Map());
  const [lastRefreshed, setLastRefreshed] = useState<string | null>(null);
  const [activeIssue, setActiveIssue] = useState<ProductIssueRead | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  // Tracked separately from `activeIssue` (which only reflects whether the
  // Sheet is open) so the list can keep showing which issue the user was
  // last looking at even after they close the drawer via the "Product
  // Issues" pipeline stage — i.e. the selection is preserved, not lost.
  const [lastSelectedIssueId, setLastSelectedIssueId] = useState<string | null>(null);

  const [statusFilter, setStatusFilter] = useState<ProductIssueStatus | "">("");
  const [categoryFilter, setCategoryFilter] = useState<FeedbackCategory | "">("");

  // Fetch-on-mount (and on filter change / manual refresh) lives entirely
  // inside the effect, as a nested function it declares and immediately
  // invokes — the canonical pattern, so a click handler can trigger the same
  // fetch by just bumping refreshToken rather than calling setState
  // synchronously from an effect. The issue LIST (with its filters and
  // pagination) stays sourced from GET /issues exactly as before; the
  // analytics calls only ever *enrich* rows with signal data that already
  // exists behind those already-shipped endpoints — nothing here is a new
  // backend capability.
  useEffect(() => {
    let cancelled = false;

    async function loadFirstPage() {
      setIsLoading(true);
      setError(null);
      try {
        const [issuesPage, overviewData, trendsData, topIssues] = await Promise.all([
          api.issues.list({
            limit: PAGE_SIZE,
            offset: 0,
            status: statusFilter || undefined,
            category: categoryFilter || undefined,
          }),
          api.analytics.overview(),
          api.analytics.trends(30),
          api.analytics.issues({ limit: 50 }),
        ]);
        if (cancelled) return;
        setIssues(issuesPage.items);
        setTotal(issuesPage.total);
        setOffset(issuesPage.items.length);
        setOverview(overviewData);
        setTrends(trendsData);
        // A Map built from an already-ranked array iterates in that same
        // insertion order — reused below for "What needs attention" so that
        // section never computes its own ranking, only filters the backend's.
        setSignalById(new Map(topIssues.items.map((item) => [item.issue_id, item])));
        setLastRefreshed(new Date().toISOString());
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
  }, [refreshToken, statusFilter, categoryFilter]);

  // Deep link from elsewhere in the app (e.g. "View in Impact Grid" after
  // processing a feedback item) — opens the same issue Sheet used for a
  // row click, just pre-selected instead of requiring a scroll/click.
  useEffect(() => {
    const issueId = new URLSearchParams(window.location.search).get("issue");
    if (!issueId) return;
    let cancelled = false;
    api.issues
      .get(issueId)
      .then((detail) => {
        if (cancelled) return;
        setActiveIssue(detail);
        setLastSelectedIssueId(detail.id);
        window.history.replaceState(null, "", window.location.pathname);
      })
      .catch(() => {
        // Stale/invalid id in the URL — not worth surfacing as an error.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function loadMore() {
    setIsLoadingMore(true);
    try {
      const page = await api.issues.list({
        limit: PAGE_SIZE,
        offset,
        status: statusFilter || undefined,
        category: categoryFilter || undefined,
      });
      setIssues((prev) => [...prev, ...page.items]);
      setOffset(offset + page.items.length);
      setTotal(page.total);
    } catch (err) {
      setError(describeApiError(err));
    } finally {
      setIsLoadingMore(false);
    }
  }

  function openIssueRow(issue: ProductIssueRead) {
    setActiveIssue(issue);
    setLastSelectedIssueId(issue.id);
  }

  async function openIssueById(issueId: string) {
    const found = issues.find((i) => i.id === issueId);
    if (found) {
      openIssueRow(found);
      return;
    }
    try {
      const detail = await api.issues.get(issueId);
      openIssueRow(detail);
    } catch {
      // Not worth surfacing — this is a secondary navigation action.
    }
  }

  const categoryCounts = issues.reduce<Record<string, number>>((acc, issue) => {
    const key = issue.category ?? "uncategorized";
    acc[key] = (acc[key] ?? 0) + 1;
    return acc;
  }, {});
  const hasActiveFilters = statusFilter !== "" || categoryFilter !== "";

  // Existing backend ranking (app.services.analytics.compute_top_issues),
  // just filtered to open issues — never a new score or re-sort of our own.
  const needsAttention = Array.from(signalById.values())
    .filter((item) => item.status === "open")
    .slice(0, 4);

  const secondaryMetrics = [
    { label: "Analyzed", value: overview?.analyzed_feedback },
    { label: "Product issues", value: overview?.total_product_issues },
    { label: "Open", value: overview?.open_product_issues },
    { label: "High severity", value: overview?.high_severity_feedback_count, accent: "text-warning" },
    { label: "Customers", value: overview?.unique_customers },
  ];

  const pipelineStages = [
    { index: "01", label: "Raw signals", onClick: () => router.push("/firehose") },
    {
      index: "02",
      label: "Product issues",
      onClick: () => document.getElementById("issues-list")?.scrollIntoView({ behavior: "smooth", block: "start" }),
    },
    { index: "03", label: "Analytics", onClick: () => router.push("/analytics") },
  ];

  return (
    <main className="mx-auto max-w-[1180px] px-6 py-12 md:px-12 md:py-16">
      {/* HERO — no enclosing card; the page background carries it */}
      <div className="mb-14 grid grid-cols-1 gap-8 md:grid-cols-[1fr_auto] md:items-end">
        <div>
          <p className="mb-3 text-xs font-medium uppercase tracking-[0.18em] text-brass">SignalForge</p>
          <h1 className="font-serif text-4xl leading-[1.05] tracking-tight text-foreground md:text-5xl">
            Product command center
          </h1>
          <p className="mt-4 max-w-[46ch] font-serif text-xl italic leading-snug text-foreground md:text-2xl">
            From customer noise to decision-grade evidence.
          </p>
          <p className="mt-4 max-w-[54ch] text-sm leading-relaxed text-muted-foreground">
            Support tickets, reviews and calls, structured into evidence your team can act on.
          </p>
        </div>
        <div className="flex flex-col items-start gap-2 md:items-end md:text-right">
          <span className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-chart-2 opacity-50" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-chart-2" />
            </span>
            Live · connected to backend
          </span>
          <span className="text-xs text-muted-foreground">
            {lastRefreshed ? <>Updated {formatRelativeTime(lastRefreshed)}</> : "Connecting…"}
          </span>
          <div className="mt-1 flex items-center gap-3">
            <button onClick={() => setRefreshToken((t) => t + 1)} className="text-xs text-muted-foreground hover:text-foreground">
              Refresh
            </button>
            <Link href="/firehose">
              <Button size="sm">Connect a source</Button>
            </Link>
          </div>
        </div>
      </div>

      {/* PIPELINE — a thin editorial process, not a rounded card */}
      <div className="mb-14 flex items-center gap-2 overflow-x-auto border-y border-border py-4 text-sm">
        {pipelineStages.map((stage, i) => (
          <div key={stage.index} className="flex items-center gap-2">
            <button
              onClick={stage.onClick}
              className="group flex items-baseline gap-2 whitespace-nowrap px-1 py-1 transition-colors"
            >
              <span className="font-mono text-xs text-brass">{stage.index}</span>
              <span className="text-muted-foreground transition-colors group-hover:text-foreground">{stage.label}</span>
            </button>
            {i < pipelineStages.length - 1 && <span className="px-2 text-muted-foreground">→</span>}
          </div>
        ))}
      </div>

      {error && (
        <div className="mb-10 border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          Couldn&rsquo;t reach the backend: {error}. Confirm it&rsquo;s running at{" "}
          <span className="font-mono">{process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000"}</span> and
          try refreshing.
        </div>
      )}

      {!error && (
        <>
          {/* METRICS — one dominant number, thin verticals, not six cards */}
          <div className="mb-16 flex flex-col gap-8 border-b border-border pb-12 md:flex-row md:items-end md:gap-12">
            <div className="shrink-0">
              <p className="font-serif text-7xl leading-none tabular-nums text-foreground md:text-8xl">
                {overview ? overview.total_feedback.toLocaleString() : isLoading ? "…" : "—"}
              </p>
              <p className="mt-3 text-sm text-muted-foreground">Feedback signals</p>
            </div>
            <div className="flex flex-1 flex-wrap gap-x-8 gap-y-5 md:justify-end">
              {secondaryMetrics.map((metric) => (
                <div key={metric.label} className="border-l border-border pl-4">
                  <p className={`font-mono text-xl tabular-nums ${metric.accent ?? "text-foreground"}`}>
                    {metric.value === undefined ? (isLoading ? "…" : "—") : metric.value.toLocaleString()}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">{metric.label}</p>
                </div>
              ))}
            </div>
          </div>

          {/* CUSTOMER SIGNAL ACTIVITY — the centerpiece; minimal container */}
          <div className="mb-16">
            <div className="mb-6 flex items-baseline justify-between border-b border-border pb-3">
              <h2 className="text-sm font-medium uppercase tracking-wide text-foreground">Customer signal activity</h2>
              <span className="text-xs text-muted-foreground">Last {trends?.window_days ?? 30} days</span>
            </div>
            {isLoading ? (
              <div className="h-52 animate-pulse bg-foreground/[0.03]" />
            ) : trends && trends.sufficient_history ? (
              <TrendChart points={trends.points} />
            ) : (
              <div className="flex h-52 items-center justify-center text-center text-sm text-muted-foreground">
                {trends?.message ?? "Not enough historical feedback to establish a reliable trend."}
              </div>
            )}
          </div>

          {/* WHAT NEEDS ATTENTION — existing backend ranking, filtered to open, never re-scored */}
          {!isLoading && needsAttention.length > 0 && (
            <div className="mb-16">
              <h2 className="mb-6 border-b border-border pb-3 text-sm font-medium uppercase tracking-wide text-warning">
                What needs attention
              </h2>
              <div className="grid grid-cols-1 gap-x-10 gap-y-1 sm:grid-cols-2">
                {needsAttention.map((item) => (
                  <button
                    key={item.issue_id}
                    onClick={() => openIssueById(item.issue_id)}
                    className="group flex items-start justify-between gap-3 border-b border-border py-3 text-left"
                  >
                    <span className="text-sm text-foreground transition-colors group-hover:text-brass">
                      {item.title}
                    </span>
                    <span className="shrink-0 whitespace-nowrap text-xs text-muted-foreground">
                      {item.feedback_count} feedback
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* ASYMMETRIC: product issues (wide) + signal mix (narrow) */}
          <div className="grid grid-cols-1 gap-12 lg:grid-cols-[1fr_320px]">
            <div id="issues-list">
              <div className="mb-6 flex flex-wrap items-end justify-between gap-3 border-b border-border pb-3">
                <h2 className="text-sm font-medium uppercase tracking-wide text-foreground">Product issues</h2>
                <div className="flex flex-wrap items-center gap-4">
                  <select
                    className={selectClass}
                    value={statusFilter}
                    onChange={(e) => setStatusFilter(e.target.value as ProductIssueStatus | "")}
                  >
                    <option value="">All statuses</option>
                    {ISSUE_STATUSES.map((status) => (
                      <option key={status} value={status}>
                        {issueStatusLabel(status)}
                      </option>
                    ))}
                  </select>
                  <select
                    className={selectClass}
                    value={categoryFilter}
                    onChange={(e) => setCategoryFilter(e.target.value as FeedbackCategory | "")}
                  >
                    <option value="">All categories</option>
                    {ISSUE_CATEGORIES.map((category) => (
                      <option key={category} value={category}>
                        {categoryLabel(category)}
                      </option>
                    ))}
                  </select>
                  {hasActiveFilters && (
                    <button
                      onClick={() => {
                        setStatusFilter("");
                        setCategoryFilter("");
                      }}
                      className="text-xs text-muted-foreground hover:text-foreground"
                    >
                      Clear
                    </button>
                  )}
                  <span className="font-mono text-xs text-muted-foreground">{total.toLocaleString()}</span>
                </div>
              </div>

              {isLoading ? (
                <div className="space-y-4">
                  {[...Array(4)].map((_, i) => (
                    <div key={i} className="h-16 animate-pulse border-b border-border bg-foreground/[0.02]" />
                  ))}
                </div>
              ) : issues.length === 0 ? (
                <div className="border-b border-border py-12 text-center text-sm text-muted-foreground">
                  {hasActiveFilters ? (
                    "No product issues match these filters."
                  ) : (
                    <>
                      No product issues have been discovered yet. Process a few customer signals from the{" "}
                      <Link href="/firehose" className="text-brass underline underline-offset-2">
                        Raw Firehose
                      </Link>{" "}
                      to build your first issue cluster.
                    </>
                  )}
                </div>
              ) : (
                <div>
                  {issues.map((issue, i) => {
                    const enrichment = signalById.get(issue.id);
                    const rowData: IssueRowData = {
                      id: issue.id,
                      title: issue.title,
                      category: issue.category,
                      affectedFeature: issue.affected_feature,
                      status: issue.status,
                      feedbackCount: issue.feedback_count,
                      customers: enrichment?.distinct_known_customers,
                      trend: enrichment?.trend_direction,
                      severity: enrichment?.average_severity,
                    };
                    return (
                      <IssueRow
                        key={issue.id}
                        issue={rowData}
                        index={i + 1}
                        onClick={() => openIssueRow(issue)}
                        selected={issue.id === lastSelectedIssueId}
                      />
                    );
                  })}
                </div>
              )}

              {!isLoading && issues.length < total && (
                <div className="mt-6 text-center">
                  <button
                    onClick={loadMore}
                    disabled={isLoadingMore}
                    className="text-xs text-muted-foreground hover:text-foreground"
                  >
                    {isLoadingMore ? "Loading…" : `Load more (${total - issues.length} remaining)`}
                  </button>
                </div>
              )}
            </div>

            <div>
              <h2 className="mb-6 border-b border-border pb-3 text-sm font-medium uppercase tracking-wide text-foreground">
                Signal mix
              </h2>
              {isLoading ? (
                <div className="space-y-3">
                  {[...Array(4)].map((_, i) => (
                    <div key={i} className="h-4 w-full animate-pulse bg-foreground/[0.03]" />
                  ))}
                </div>
              ) : Object.keys(categoryCounts).length > 0 ? (
                <CategoryDistribution counts={categoryCounts} />
              ) : (
                <p className="text-sm text-muted-foreground">No category data yet.</p>
              )}

              {!isLoading && issues.length > 0 && (
                <div className="mt-10">
                  <p className="mb-4 text-xs text-muted-foreground">By feedback volume</p>
                  <IssueVolumeChart issues={issues} />
                </div>
              )}
            </div>
          </div>
        </>
      )}

      <Sheet open={!!activeIssue} onOpenChange={(isOpen) => !isOpen && setActiveIssue(null)}>
        <SheetContent side="right" className="w-full border-none p-0 sm:max-w-3xl">
          <DecisionBrief issue={activeIssue} onClose={() => setActiveIssue(null)} />
        </SheetContent>
      </Sheet>
    </main>
  );
}

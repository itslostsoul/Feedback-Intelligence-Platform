"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, describeApiError, type ProductIssueRead } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import DecisionBrief from "@/components/ui/DecisionBrief";
import { categoryBadgeClass, categoryLabel, formatRelativeTime } from "@/lib/format";

const PAGE_SIZE = 20;

interface Stats {
  totalIssues: number;
  openIssues: number;
  feedbackSignals: number;
  decisionsEvaluated: number;
}

export default function Dashboard() {
  const [issues, setIssues] = useState<ProductIssueRead[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [lastRefreshed, setLastRefreshed] = useState<string | null>(null);
  const [activeIssue, setActiveIssue] = useState<ProductIssueRead | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  // Fetch-on-mount (and on manual refresh) lives entirely inside the effect,
  // as a nested function it declares and immediately invokes — the
  // canonical pattern, so a click handler can trigger the same fetch by
  // just bumping refreshToken rather than calling setState synchronously
  // from an effect.
  useEffect(() => {
    let cancelled = false;

    async function loadFirstPage() {
      setIsLoading(true);
      setError(null);
      try {
        const [issuesPage, openPage, feedbackPage, decisionsPage] = await Promise.all([
          api.issues.list({ limit: PAGE_SIZE, offset: 0 }),
          api.issues.list({ limit: 1, status: "open" }),
          api.feedback.list({ limit: 1 }),
          api.decisions.list({ limit: 1 }),
        ]);
        if (cancelled) return;
        setIssues(issuesPage.items);
        setTotal(issuesPage.total);
        setOffset(issuesPage.items.length);
        setStats({
          totalIssues: issuesPage.total,
          openIssues: openPage.total,
          feedbackSignals: feedbackPage.total,
          decisionsEvaluated: decisionsPage.total,
        });
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
  }, [refreshToken]);

  async function loadMore() {
    setIsLoadingMore(true);
    try {
      const page = await api.issues.list({ limit: PAGE_SIZE, offset });
      setIssues((prev) => [...prev, ...page.items]);
      setOffset(offset + page.items.length);
      setTotal(page.total);
    } catch (err) {
      setError(describeApiError(err));
    } finally {
      setIsLoadingMore(false);
    }
  }

  const categoryCounts = issues.reduce<Record<string, number>>((acc, issue) => {
    const key = issue.category ?? "uncategorized";
    acc[key] = (acc[key] ?? 0) + 1;
    return acc;
  }, {});
  const maxCategoryCount = Math.max(1, ...Object.values(categoryCounts));

  return (
    <main className="mx-auto max-w-[1180px] px-6 py-10 md:px-12">
      {/* utility bar — status, not a second nav */}
      <div className="mb-8 flex items-center justify-between">
        <p className="font-mono text-xs text-muted-foreground">
          {lastRefreshed ? (
            <>
              Last refreshed <span className="text-foreground">{formatRelativeTime(lastRefreshed)}</span>
            </>
          ) : (
            "Connecting to backend…"
          )}
          <span className="mx-2 text-border">/</span>
          <button onClick={() => setRefreshToken((t) => t + 1)} className="text-foreground hover:underline">
            Refresh
          </button>
        </p>
        <Link href="/firehose">
          <Button variant="outline" size="sm">
            Connect a source
          </Button>
        </Link>
      </div>

      {/* PANEL */}
      <div className="overflow-hidden rounded-xl border border-border bg-card">
        <div className="border-b border-border p-8 md:p-10">
          <p className="mb-2 font-mono text-xs uppercase tracking-wider text-muted-foreground">
            Product intelligence
          </p>
          <h1 className="mb-2 text-3xl font-semibold tracking-tight text-foreground md:text-4xl">
            Where every signal becomes a decision
          </h1>
          <p className="max-w-[46ch] text-sm text-muted-foreground">
            Support tickets, reviews and calls, structured into evidence your team can act on.
          </p>

          {error && (
            <div className="mt-8 rounded-lg border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
              Couldn&rsquo;t reach the backend: {error}. Confirm it&rsquo;s running at{" "}
              <span className="font-mono">{process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000"}</span>{" "}
              and try refreshing.
            </div>
          )}

          {/* CATEGORY BREAKDOWN — real, derived from the currently loaded issues */}
          {!error && (
            <div className="mt-10">
              <div className="flex h-16 items-end gap-3">
                {Object.entries(categoryCounts).length === 0
                  ? [...Array(5)].map((_, i) => (
                      <div key={i} className="h-full flex-1 animate-pulse rounded-t-sm bg-muted" />
                    ))
                  : Object.entries(categoryCounts).map(([key, count]) => (
                      <div key={key} className="flex flex-1 flex-col items-center justify-end gap-1">
                        <div
                          className="w-full rounded-t-sm bg-primary"
                          style={{
                            height: `${Math.max(8, (count / maxCategoryCount) * 100)}%`,
                            opacity: 0.4 + (count / maxCategoryCount) * 0.5,
                          }}
                        />
                      </div>
                    ))}
              </div>
              <div className="mt-2 flex gap-3">
                {Object.entries(categoryCounts).map(([key, count]) => (
                  <p key={key} className="flex-1 truncate text-center font-mono text-[10px] text-muted-foreground">
                    {key === "uncategorized" ? "Uncategorized" : categoryLabel(key as never)} ({count})
                  </p>
                ))}
              </div>
              <p className="mt-2 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                Issue categories — currently loaded page
              </p>
            </div>
          )}

          {/* PIPELINE STATS — real counts, no invented scores */}
          <div className="mt-10 grid grid-cols-2 gap-6 md:grid-cols-4">
            {[
              { label: "Product issues", value: stats?.totalIssues, accent: "text-muted-foreground" },
              { label: "Open issues", value: stats?.openIssues, accent: "text-chart-1" },
              { label: "Feedback ingested", value: stats?.feedbackSignals, accent: "text-chart-2" },
              { label: "Decisions evaluated", value: stats?.decisionsEvaluated, accent: "text-chart-2" },
            ].map((stage) => (
              <div key={stage.label} className="border-l-2 border-border pl-4">
                <p className={`font-mono text-2xl ${stage.accent}`}>
                  {stage.value === undefined ? (isLoading ? "…" : "—") : stage.value.toLocaleString()}
                </p>
                <p className="mt-1 text-[11px] uppercase tracking-wider text-muted-foreground">{stage.label}</p>
              </div>
            ))}
          </div>
        </div>

        {/* TABLE */}
        <Table>
          <TableHeader>
            <TableRow className="border-border hover:bg-transparent">
              <TableHead className="h-11 pl-8 font-mono text-[11px] uppercase tracking-wider text-muted-foreground md:pl-10">
                Issue
              </TableHead>
              <TableHead className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
                AI category
              </TableHead>
              <TableHead className="pr-8 text-right font-mono text-[11px] uppercase tracking-wider text-muted-foreground md:pr-10">
                Feedback count
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading
              ? [...Array(5)].map((_, index) => (
                  <TableRow key={index} className="border-border hover:bg-transparent">
                    <TableCell className="py-5 pl-8 md:pl-10">
                      <div className="h-4 w-3/4 animate-pulse rounded bg-muted" />
                    </TableCell>
                    <TableCell>
                      <div className="h-5 w-20 animate-pulse rounded-sm bg-muted" />
                    </TableCell>
                    <TableCell className="py-5 pr-8 md:pr-10">
                      <div className="ml-auto h-6 w-8 animate-pulse rounded bg-muted" />
                    </TableCell>
                  </TableRow>
                ))
              : issues.length === 0 && !error
                ? (
                    <TableRow className="border-border hover:bg-transparent">
                      <TableCell colSpan={3} className="py-10 text-center text-sm text-muted-foreground">
                        No product issues yet. Ingest feedback and run discovery from the{" "}
                        <Link href="/firehose" className="underline">
                          Raw Firehose
                        </Link>{" "}
                        page to get started.
                      </TableCell>
                    </TableRow>
                  )
                : issues.map((issue) => (
                    <TableRow
                      key={issue.id}
                      onClick={() => setActiveIssue(issue)}
                      className="cursor-pointer border-border hover:bg-accent/40"
                    >
                      <TableCell className="whitespace-normal py-5 pl-8 font-medium text-foreground md:pl-10">
                        {issue.title}
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant="outline"
                          className={`rounded-sm text-xs font-medium ${categoryBadgeClass(issue.category)}`}
                        >
                          {categoryLabel(issue.category)}
                        </Badge>
                      </TableCell>
                      <TableCell className="py-5 pr-8 text-right font-mono text-base text-foreground md:pr-10">
                        {issue.feedback_count}
                      </TableCell>
                    </TableRow>
                  ))}
          </TableBody>
        </Table>

        {!isLoading && issues.length < total && (
          <div className="border-t border-border bg-muted/40 p-4 text-center">
            <button
              onClick={loadMore}
              disabled={isLoadingMore}
              className="text-xs font-medium tracking-wide text-muted-foreground hover:text-foreground"
            >
              {isLoadingMore ? "Loading…" : `Load more (${total - issues.length} remaining)`}
            </button>
          </div>
        )}
      </div>

      <Sheet open={!!activeIssue} onOpenChange={(isOpen) => !isOpen && setActiveIssue(null)}>
        <SheetContent side="right" className="w-full border-none p-0 sm:max-w-2xl">
          <DecisionBrief issue={activeIssue} />
        </SheetContent>
      </Sheet>
    </main>
  );
}

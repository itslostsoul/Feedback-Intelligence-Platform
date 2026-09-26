"use client";

import { useEffect, useRef, useState } from "react";
import {
  api,
  describeApiError,
  type AnalyticsCategoriesResponse,
  type AnalyticsEvidenceFact,
  type AnalyticsInsightResult,
  type AnalyticsOverview,
  type AnalyticsSourcesResponse,
  type AnalyticsTrendsResponse,
  type InsightClaim,
  type ProductIssueRead,
  type TopIssuesResponse,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { IssueRow, type IssueRowData } from "@/components/ui/IssueRow";
import DecisionBrief from "@/components/ui/DecisionBrief";
import { CategoryDistribution, TrendChart } from "@/components/ui/DashboardCharts";
import { formatPercent } from "@/lib/format";
import { Sparkles } from "lucide-react";

type InsightState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; data: AnalyticsInsightResult }
  | { status: "error"; message: string };

/** Short, human label for one evidence chip, derived entirely from the
 * citation_id's own convention (`analytics:<...>`) and its resolved value —
 * never a separate guess at what the fact "means". */
function formatEvidenceChip(citationId: string, fact: AnalyticsEvidenceFact): string {
  const value = fact.value as unknown;
  if (citationId === "analytics:feedback_volume") return `${value} feedback`;
  if (citationId === "analytics:analysis_coverage") return `${formatPercent(value as number)} analyzed`;
  if (citationId === "analytics:severity:average") return `avg severity ${value}`;
  if (citationId === "analytics:severity:high_count") return `${value} high severity`;
  if (citationId === "analytics:customers:unique") return `${value} customers`;
  if (citationId === "analytics:issues:open_count") return `${value} open issues`;
  if (citationId === "analytics:issues:resolved_count") return `${value} resolved issues`;
  if (citationId === "analytics:trend:recent" && value && typeof value === "object") {
    const v = value as { direction: string };
    return `${v.direction} trend`;
  }
  if (citationId.startsWith("analytics:category:") && value && typeof value === "object") {
    const v = value as { count: number };
    return `${v.count} ${citationId.split(":")[2]}`;
  }
  if (citationId.startsWith("analytics:source:") && value && typeof value === "object") {
    const v = value as { feedback_count: number };
    return `${v.feedback_count} from ${citationId.split(":").slice(2).join(":")}`;
  }
  if (citationId.endsWith(":feedback_count")) return `${value} feedback`;
  if (citationId.endsWith(":trend")) return `${value} trend`;
  if (citationId.endsWith(":severity")) return `severity ${value}`;
  if (citationId.endsWith(":customers")) return `${value} customers`;
  if (citationId.endsWith(":duplicate_ratio")) return `${formatPercent(value as number)} duplicate`;
  if (citationId.endsWith(":source_diversity")) return `${value} sources`;
  return fact.label;
}

function issueIdFromCitation(citationId: string): string | null {
  const match = citationId.match(/^analytics:issue:([0-9a-fA-F-]+):/);
  return match ? match[1] : null;
}

export default function AnalyticsPage() {
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [trends, setTrends] = useState<AnalyticsTrendsResponse | null>(null);
  const [categories, setCategories] = useState<AnalyticsCategoriesResponse | null>(null);
  const [sources, setSources] = useState<AnalyticsSourcesResponse | null>(null);
  const [topIssues, setTopIssues] = useState<TopIssuesResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);
  const [lastRefreshed, setLastRefreshed] = useState<string | null>(null);

  const [activeIssue, setActiveIssue] = useState<ProductIssueRead | null>(null);
  const [insight, setInsight] = useState<InsightState>({ status: "idle" });

  const summaryRef = useRef<HTMLDivElement>(null);
  const categoriesRef = useRef<HTMLDivElement>(null);
  const sourcesRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setIsLoading(true);
      setError(null);
      try {
        const [ov, tr, cat, src, iss] = await Promise.all([
          api.analytics.overview(),
          api.analytics.trends(30),
          api.analytics.categories(),
          api.analytics.sources(),
          api.analytics.issues({ limit: 10 }),
        ]);
        if (cancelled) return;
        setOverview(ov);
        setTrends(tr);
        setCategories(cat);
        setSources(src);
        setTopIssues(iss);
        setLastRefreshed(new Date().toISOString());
      } catch (err) {
        if (!cancelled) setError(describeApiError(err));
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [refreshToken]);

  async function handleGenerateInsight() {
    setInsight({ status: "loading" });
    try {
      const data = await api.analytics.generateInsights();
      setInsight({ status: "ready", data });
    } catch (err) {
      setInsight({ status: "error", message: describeApiError(err) });
    }
  }

  async function openIssue(issueId: string) {
    try {
      const detail = await api.issues.get(issueId);
      setActiveIssue(detail);
    } catch {
      // Issue may have since been removed — not worth surfacing as an error
      // from what is, for this page, a secondary navigation action.
    }
  }

  function navigateToEvidence(citationId: string) {
    const issueId = issueIdFromCitation(citationId);
    if (issueId) {
      openIssue(issueId);
      return;
    }
    const target = citationId.startsWith("analytics:category:")
      ? categoriesRef
      : citationId.startsWith("analytics:source:")
        ? sourcesRef
        : summaryRef;
    target.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  const categoryCounts: Record<string, number> = {};
  categories?.items.forEach((item) => {
    categoryCounts[item.category] = item.count;
  });
  const sourceCounts: Record<string, number> = {};
  sources?.items.forEach((item) => {
    sourceCounts[item.source] = item.feedback_count;
  });

  const secondaryMetrics = [
    { label: "Analyzed", value: overview?.analyzed_feedback },
    { label: "Issues", value: overview?.total_product_issues },
    { label: "Open", value: overview?.open_product_issues },
    { label: "High severity", value: overview?.high_severity_feedback_count, accent: "text-warning" },
    { label: "Customers", value: overview?.unique_customers },
  ];

  return (
    <main className="mx-auto max-w-[1180px] px-6 py-12 md:px-12 md:py-16">
      {/* REPORT HEADER — a document header, not a hero */}
      <div className="mb-14 flex flex-wrap items-start justify-between gap-6 border-b-2 border-foreground pb-6">
        <div>
          <h1 className="font-serif text-3xl tracking-tight text-foreground md:text-4xl">AI Analytics</h1>
          <p className="mt-2 text-sm text-muted-foreground">Customer signal intelligence, calculated from PostgreSQL.</p>
          {lastRefreshed && !error && (
            <p className="mt-3 font-mono text-[11px] text-muted-foreground/70">
              As of {new Date(overview?.calculated_at ?? lastRefreshed).toLocaleString()}
            </p>
          )}
        </div>
        <div className="flex items-center gap-3">
          <Button variant="outline" size="sm" onClick={() => setRefreshToken((t) => t + 1)} disabled={isLoading}>
            {isLoading ? "Refreshing…" : "Refresh"}
          </Button>
          <Button size="sm" onClick={handleGenerateInsight} disabled={insight.status === "loading" || isLoading || !!error}>
            <Sparkles className="h-3.5 w-3.5" />
            {insight.status === "loading" ? "Analyzing…" : "Generate AI analysis"}
          </Button>
        </div>
      </div>

      {error && (
        <div className="mb-8 border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          Couldn&rsquo;t reach the backend: {error}. Try refreshing.
        </div>
      )}

      {!error && (
        <div className="space-y-16">
          {/* 01 — EXECUTIVE SIGNAL SUMMARY */}
          <section ref={summaryRef}>
            <SectionIndex n="01" title="Executive signal summary" />
            <div className="grid grid-cols-2 gap-x-8 gap-y-6 sm:grid-cols-3 md:grid-cols-6">
              <div>
                <p className="font-serif text-5xl tabular-nums text-foreground">
                  {overview ? overview.total_feedback.toLocaleString() : isLoading ? "…" : "—"}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">Total signals</p>
              </div>
              {secondaryMetrics.map((metric) => (
                <div key={metric.label} className="border-l border-border pl-4">
                  <p className={`font-mono text-2xl tabular-nums ${metric.accent ?? "text-foreground"}`}>
                    {metric.value === undefined ? (isLoading ? "…" : "—") : metric.value.toLocaleString()}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">{metric.label}</p>
                </div>
              ))}
            </div>
            {overview && (
              <p className="mt-6 text-xs text-muted-foreground">
                Analysis coverage {formatPercent(overview.analysis_coverage)} · Average severity{" "}
                {overview.average_severity ?? "—"} · {overview.resolved_product_issues} resolved issues
              </p>
            )}
          </section>

          {/* 02 — SIGNAL ACTIVITY */}
          <section>
            <SectionIndex n="02" title="Signal activity" />
            <p className="mb-6 text-sm text-muted-foreground">
              Customer signal trend, last {trends?.window_days ?? 30} days.
            </p>
            {isLoading ? (
              <div className="h-52 animate-pulse bg-foreground/[0.03]" />
            ) : trends && trends.sufficient_history ? (
              <TrendChart points={trends.points} />
            ) : (
              <div className="flex h-52 items-center justify-center text-center text-sm text-muted-foreground">
                {trends?.message ?? "Not enough historical feedback to establish a reliable trend."}
              </div>
            )}
          </section>

          {/* 03 — CATEGORY / SOURCE SIGNALS */}
          <section className="grid grid-cols-1 gap-12 lg:grid-cols-2">
            <div ref={categoriesRef}>
              <SectionIndex n="03" title="Category signals" />
              {isLoading ? (
                <div className="space-y-3">
                  {[...Array(5)].map((_, i) => (
                    <div key={i} className="h-4 w-full animate-pulse bg-foreground/[0.03]" />
                  ))}
                </div>
              ) : (
                <CategoryDistribution counts={categoryCounts} />
              )}
            </div>

            <div ref={sourcesRef}>
              <SectionIndex n="04" title="Source signals" />
              {isLoading ? (
                <div className="space-y-3">
                  {[...Array(4)].map((_, i) => (
                    <div key={i} className="h-4 w-full animate-pulse bg-foreground/[0.03]" />
                  ))}
                </div>
              ) : (
                <CategoryDistribution counts={sourceCounts} />
              )}
            </div>
          </section>

          {/* 05 — PRODUCT ISSUE INTELLIGENCE */}
          <section>
            <div className="mb-2 flex flex-wrap items-baseline justify-between gap-3">
              <SectionIndex n="05" title="Product issue intelligence" noMargin />
              {topIssues && <p className="text-xs text-muted-foreground">{topIssues.ranking_method.split(",")[0]}</p>}
            </div>
            <div className="mt-6">
              {isLoading
                ? [...Array(3)].map((_, i) => (
                    <div key={i} className="h-16 animate-pulse border-b border-border bg-foreground/[0.02]" />
                  ))
                : !topIssues || topIssues.items.length === 0
                  ? (
                      <div className="border-b border-border py-12 text-center text-sm text-muted-foreground">
                        No product issues discovered yet.
                      </div>
                    )
                  : topIssues.items.map((item, i) => {
                      const rowData: IssueRowData = {
                        id: item.issue_id,
                        title: item.title,
                        category: item.category,
                        affectedFeature: item.affected_feature,
                        status: item.status,
                        feedbackCount: item.feedback_count,
                        customers: item.distinct_known_customers,
                        trend: item.trend_direction,
                        severity: item.average_severity,
                      };
                      return <IssueRow key={item.issue_id} issue={rowData} index={i + 1} onClick={() => openIssue(item.issue_id)} />;
                    })}
            </div>
          </section>

          {/* 06 — AI ANALYSIS — the one place a deliberate dark, contrasting
              panel is used, so "this is an interpretation, not a fact" is
              communicated visually, not just in words. */}
          <section className="bg-foreground px-6 py-10 text-background sm:px-10 md:py-14">
            <div className="mb-8 flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-chart-2" />
              <p className="text-xs font-medium uppercase tracking-[0.14em] text-background/60">AI Analysis</p>
              <span className="border border-background/25 px-2 py-0.5 text-[10px] uppercase tracking-wide text-background/70">
                Evidence-grounded
              </span>
            </div>

            {insight.status === "idle" && (
              <p className="max-w-[60ch] text-sm leading-relaxed text-background/70">
                Generate a grounded interpretation of the analytics above — every claim will cite the exact
                aggregate it&rsquo;s based on.
              </p>
            )}
            {insight.status === "loading" && (
              <p className="text-sm text-background/70">Interpreting the current analytics evidence…</p>
            )}
            {insight.status === "error" && (
              <div className="border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
                AI analysis unavailable: {insight.message}. The analytics above are unaffected and still reflect
                live PostgreSQL data.
              </div>
            )}
            {insight.status === "ready" && (
              <div className="space-y-8">
                <p className="max-w-[62ch] font-serif text-2xl italic leading-snug text-background md:text-3xl">
                  &ldquo;{insight.data.headline}&rdquo;
                </p>
                <p className="font-mono text-[11px] text-background/50">
                  {insight.data.provider} · {insight.data.model} · {new Date(insight.data.generated_at).toLocaleString()}
                </p>

                <InsightSection
                  title="Key findings"
                  claims={insight.data.key_findings}
                  evidence={insight.data.evidence}
                  onNavigate={navigateToEvidence}
                />
                <InsightSection
                  title="Emerging patterns"
                  claims={insight.data.emerging_patterns}
                  evidence={insight.data.evidence}
                  onNavigate={navigateToEvidence}
                />
                <InsightSection
                  title="Areas to investigate"
                  claims={insight.data.areas_to_investigate}
                  evidence={insight.data.evidence}
                  onNavigate={navigateToEvidence}
                />
                <InsightSection
                  title="Caveats"
                  claims={insight.data.caveats}
                  evidence={insight.data.evidence}
                  onNavigate={navigateToEvidence}
                />
              </div>
            )}
          </section>
        </div>
      )}

      <Sheet open={!!activeIssue} onOpenChange={(isOpen) => !isOpen && setActiveIssue(null)}>
        <SheetContent side="right" className="w-full border-none p-0 sm:max-w-3xl">
          <DecisionBrief issue={activeIssue} onClose={() => setActiveIssue(null)} />
        </SheetContent>
      </Sheet>
    </main>
  );
}

function SectionIndex({ n, title, noMargin }: { n: string; title: string; noMargin?: boolean }) {
  return (
    <div className={`flex items-baseline gap-3 border-b border-border pb-3 ${noMargin ? "" : "mb-6"}`}>
      <span className="font-mono text-xs text-brass">{n}</span>
      <h2 className="text-sm font-medium uppercase tracking-wide text-foreground">{title}</h2>
    </div>
  );
}

function InsightSection({
  title,
  claims,
  evidence,
  onNavigate,
}: {
  title: string;
  claims: InsightClaim[];
  evidence: Record<string, AnalyticsEvidenceFact>;
  onNavigate: (citationId: string) => void;
}) {
  if (claims.length === 0) return null;
  return (
    <div>
      <p className="mb-3 text-xs font-medium uppercase tracking-wide text-background/50">{title}</p>
      <ul className="space-y-3">
        {claims.map((claim, i) => (
          <li key={i} className="leading-relaxed text-background/85">
            {claim.text}{" "}
            <span className="ml-1 inline-flex flex-wrap gap-1 align-middle">
              {claim.evidence_ids.map((id) => {
                const fact = evidence[id];
                if (!fact) return null;
                return (
                  <button
                    key={id}
                    onClick={() => onNavigate(id)}
                    title={fact.label}
                    className="border border-background/25 px-2 py-0.5 font-mono text-[10px] text-background/70 transition-colors hover:border-chart-2/60 hover:text-chart-2"
                  >
                    {formatEvidenceChip(id, fact)}
                  </button>
                );
              })}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

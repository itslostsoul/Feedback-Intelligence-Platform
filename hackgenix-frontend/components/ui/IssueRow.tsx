"use client";

import { cn } from "cn";
import type { FeedbackCategory, ProductIssueStatus } from "@/lib/api";
import { categoryLabel } from "@/lib/format";

/**
 * The shape one "issue intelligence" row needs — a superset of what a plain
 * ProductIssueRead carries (title/category/status/feedback_count) plus the
 * signal-derived fields (customers/trend/severity) that only exist once
 * app.services.issue_signals has run for that issue. Callers that don't have
 * the enriched fields simply omit them; the row renders what it's given and
 * never fabricates the rest — see the `undefined`-safe formatting below.
 */
export interface IssueRowData {
  id: string;
  title: string;
  category: FeedbackCategory | null;
  affectedFeature?: string | null;
  status: ProductIssueStatus;
  feedbackCount: number;
  customers?: number | null;
  trend?: string | null;
  severity?: number | null;
}

const STATUS_LABEL: Record<ProductIssueStatus, string> = {
  open: "Open",
  resolved: "Resolved",
  dismissed: "Dismissed",
};

const STATUS_TEXT_CLASS: Record<ProductIssueStatus, string> = {
  open: "text-chart-2",
  resolved: "text-success",
  dismissed: "text-muted-foreground",
};

function trendLabel(trend?: string | null): string | null {
  if (!trend || trend === "insufficient_data") return null;
  return trend.charAt(0).toUpperCase() + trend.slice(1);
}

/** One issue as an editorial index row — a number, a headline, a line of
 * supporting facts, and a thin rule — rather than a boxed card. Shared by
 * the Overview and Analytics pages so both read as the same product. */
export function IssueRow({
  issue,
  index,
  onClick,
  selected,
}: {
  issue: IssueRowData;
  index: number;
  onClick: () => void;
  selected?: boolean;
}) {
  const trend = trendLabel(issue.trend);
  const metaParts = [
    categoryLabel(issue.category),
    `${issue.feedbackCount.toLocaleString()} feedback`,
    issue.customers != null ? `${issue.customers} customer${issue.customers === 1 ? "" : "s"}` : null,
    trend,
    issue.severity != null ? `severity ${issue.severity.toFixed(1)}` : null,
  ].filter((part): part is string => !!part);

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "group flex w-full items-start gap-5 border-b border-border py-5 text-left transition-colors first:pt-0",
        selected && "bg-surface-4/40"
      )}
    >
      <span className="w-7 shrink-0 pt-0.5 font-mono text-xs tabular-nums text-muted-foreground">
        {String(index).padStart(2, "0")}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[15px] leading-snug text-foreground transition-colors group-hover:text-brass">
          {issue.title}
        </p>
        <p className="mt-1.5 text-xs text-muted-foreground">{metaParts.join(" · ")}</p>
      </div>
      <span
        className={cn(
          "shrink-0 pt-0.5 text-xs font-medium uppercase tracking-wide",
          STATUS_TEXT_CLASS[issue.status]
        )}
      >
        {STATUS_LABEL[issue.status]}
      </span>
    </button>
  );
}

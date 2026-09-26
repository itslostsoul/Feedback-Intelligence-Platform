import type { FeedbackCategory, ProcessingStatus, ProductIssueStatus, SourceType } from "@/lib/api";

export function categoryLabel(category: FeedbackCategory | null | undefined): string {
  switch (category) {
    case "bug":
      return "Bug";
    case "feature_request":
      return "Feature request";
    case "ui_friction":
      return "UI friction";
    case "performance":
      return "Performance";
    case "billing":
      return "Billing";
    default:
      return "Uncategorized";
  }
}

/** Tailwind classes keyed to the same chart-1..5 palette used across the UI. */
export function categoryBadgeClass(category: FeedbackCategory | null | undefined): string {
  switch (category) {
    case "bug":
      return "bg-chart-1/15 text-chart-1 border-chart-1/25";
    case "performance":
      return "bg-chart-5/15 text-chart-5 border-chart-5/25";
    case "ui_friction":
      return "bg-chart-3/15 text-chart-3 border-chart-3/25";
    case "feature_request":
      return "bg-chart-2/15 text-chart-2 border-chart-2/25";
    case "billing":
      return "bg-chart-4/15 text-chart-4 border-chart-4/25";
    default:
      return "bg-muted text-muted-foreground border-border";
  }
}

/** Solid-fill class for the same chart-1..5 mapping as {@link categoryBadgeClass} —
 * used where a category needs a small color chip/bar rather than a badge, so
 * a category's color stays identical everywhere it appears in the UI. */
export function categoryDotClass(category: FeedbackCategory | null | undefined): string {
  switch (category) {
    case "bug":
      return "bg-chart-1";
    case "performance":
      return "bg-chart-5";
    case "ui_friction":
      return "bg-chart-3";
    case "feature_request":
      return "bg-chart-2";
    case "billing":
      return "bg-chart-4";
    default:
      return "bg-muted-foreground/40";
  }
}

export function sourceTypeLabel(sourceType: SourceType | null | undefined): string {
  switch (sourceType) {
    case "review":
      return "Review";
    case "support_ticket":
      return "Support ticket";
    case "survey":
      return "Survey";
    case "social":
      return "Social";
    case "email":
      return "Email";
    case "interview":
      return "Interview";
    default:
      return "Other";
  }
}

export function processingStatusLabel(status: ProcessingStatus): string {
  switch (status) {
    case "received":
      return "Received";
    case "pending_analysis":
      return "Pending analysis";
    case "analyzing":
      return "Analyzing";
    case "analyzed":
      return "Analyzed";
    case "needs_review":
      return "Needs review";
    case "failed":
      return "Failed";
  }
}

export function issueStatusLabel(status: ProductIssueStatus | string | null | undefined): string {
  switch (status) {
    case "open":
      return "Open";
    case "resolved":
      return "Resolved";
    case "dismissed":
      return "Dismissed";
    default:
      return status ?? "Unknown";
  }
}

/** Compact "3m ago" / "2h ago" / "5d ago" style relative time. */
export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "unknown";
  const diffMs = Date.now() - then;
  const diffSec = Math.max(0, Math.round(diffMs / 1000));
  if (diffSec < 60) return "just now";
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  return `${diffDay}d ago`;
}

export function formatPercent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

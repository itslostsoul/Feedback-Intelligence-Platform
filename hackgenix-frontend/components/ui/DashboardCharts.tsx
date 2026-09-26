"use client";

import type { DailyTrendPoint } from "@/lib/api";
import { categoryLabel } from "@/lib/format";
import type { FeedbackCategory } from "@/lib/api";

const MAX_VOLUME_ROWS = 8;

function shortenTitle(title: string, max = 34): string {
  return title.length > max ? `${title.slice(0, max - 1).trimEnd()}…` : title;
}

/**
 * "Feedback volume by product issue" — a restrained horizontal bar chart.
 * Driven entirely by the ProductIssueRead[] the dashboard already loaded;
 * never fetches or invents its own data. Ranked by feedback_count (the
 * natural read for a volume chart) and capped at MAX_VOLUME_ROWS so a large
 * loaded page doesn't turn this into an overwhelming wall of bars.
 */
export function IssueVolumeChart({
  issues,
}: {
  issues: { id: string; title: string; feedback_count: number }[];
}) {
  if (issues.length === 0) {
    return (
      <div className="flex h-full min-h-[120px] items-center justify-center text-sm text-muted-foreground">
        No product issues loaded yet.
      </div>
    );
  }

  const ranked = [...issues].sort((a, b) => b.feedback_count - a.feedback_count).slice(0, MAX_VOLUME_ROWS);
  const max = Math.max(1, ...ranked.map((i) => i.feedback_count));
  const hiddenCount = issues.length - ranked.length;

  return (
    <div>
      <div className="space-y-3.5">
        {ranked.map((issue) => {
          const pct = Math.max(3, (issue.feedback_count / max) * 100);
          return (
            <div key={issue.id} className="group relative">
              <div className="mb-1.5 flex items-baseline justify-between gap-3">
                <span className="truncate text-xs text-muted-foreground">{shortenTitle(issue.title)}</span>
                <span className="shrink-0 font-mono text-xs text-foreground">{issue.feedback_count}</span>
              </div>
              <div className="h-[3px] w-full overflow-hidden bg-foreground/[0.07]">
                <div
                  className="h-full transition-[width] duration-500 ease-out"
                  style={{
                    width: `${pct}%`,
                    background: "linear-gradient(90deg, var(--brass), color-mix(in oklch, var(--brass), var(--foreground) 30%))",
                  }}
                />
              </div>

              {/* hover tooltip — full title + exact count, never truncated data */}
              <div
                role="tooltip"
                className="pointer-events-none absolute left-0 top-full z-10 mt-1.5 max-w-xs border border-border bg-popover px-2.5 py-1.5 text-xs text-popover-foreground opacity-0 shadow-md transition-opacity group-hover:opacity-100"
              >
                <p className="font-medium">{issue.title}</p>
                <p className="font-mono text-[10px] text-muted-foreground">{issue.feedback_count} feedback item(s)</p>
              </div>
            </div>
          );
        })}
      </div>
      {hiddenCount > 0 && (
        <p className="mt-3 text-[11px] text-muted-foreground">
          Showing top {ranked.length} of {issues.length} loaded issues
        </p>
      )}
    </div>
  );
}

/**
 * Editorial category/source distribution — a label, a thin proportional
 * rule, and a right-aligned count. Deliberately monochrome (brass) rather
 * than per-category colored dots, matching the "data panel" look used
 * throughout Analytics; category identity is still color-coded wherever a
 * Badge is shown instead (categoryBadgeClass), just not duplicated here.
 */
export function CategoryDistribution({ counts }: { counts: Record<string, number> }) {
  const rows = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...rows.map(([, count]) => count));

  return (
    <div className="space-y-3">
      {rows.map(([key, count]) => {
        const category = key === "uncategorized" ? null : (key as FeedbackCategory);
        const pct = Math.max(4, (count / max) * 100);
        return (
          <div key={key}>
            <div className="mb-1 flex items-baseline justify-between gap-3">
              <span className="text-xs text-muted-foreground">
                {key === "uncategorized" ? "Uncategorized" : categoryLabel(category)}
              </span>
              <span className="font-mono text-xs text-foreground">{count}</span>
            </div>
            <div className="h-[3px] w-full overflow-hidden bg-foreground/[0.07]">
              <div className="h-full bg-brass/70 transition-[width] duration-300" style={{ width: `${pct}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function formatShortDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
}

/**
 * "Feedback trend" line chart — two real series (total feedback, high
 * severity) over app.services.analytics's own daily buckets. Renders as
 * plain SVG polylines over a 0-100 viewBox (no charting library installed);
 * hover targets are absolutely-positioned HTML overlays, same technique as
 * the tooltip on IssueVolumeChart above. Never drawn when the caller's own
 * `sufficientHistory` flag is false. Ink for the primary line, a soft brass
 * area fill, warning-orange for the secondary series, and a single restrained
 * cyan dot on the latest point — cyan stays reserved for "live" elsewhere.
 */
export function TrendChart({ points }: { points: DailyTrendPoint[] }) {
  if (points.length < 2) {
    return (
      <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
        Not enough daily buckets to draw a trend line yet.
      </div>
    );
  }

  const maxValue = Math.max(1, ...points.map((p) => p.feedback_count));
  const toXY = (index: number, value: number) => {
    const x = (index / (points.length - 1)) * 100;
    const y = 100 - (value / maxValue) * 100;
    return { x, y };
  };

  const feedbackPolyline = points.map((p, i) => toXY(i, p.feedback_count)).map(({ x, y }) => `${x},${y}`).join(" ");
  const severityPolyline = points
    .map((p, i) => toXY(i, p.high_severity_count))
    .map(({ x, y }) => `${x},${y}`)
    .join(" ");
  const areaPolygon = `0,100 ${feedbackPolyline} 100,100`;
  const latest = toXY(points.length - 1, points[points.length - 1].feedback_count);

  const gridLines = [25, 50, 75];

  return (
    <div>
      <div className="mb-4 flex items-center gap-4">
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span className="h-1.5 w-1.5 rounded-full bg-foreground" aria-hidden="true" />
          Feedback
        </span>
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span className="h-1.5 w-1.5 rounded-full bg-warning" aria-hidden="true" />
          High severity
        </span>
      </div>

      <div className="relative h-52 w-full">
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="h-full w-full overflow-visible">
          <defs>
            <linearGradient id="trend-area-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--brass)" stopOpacity="0.20" />
              <stop offset="100%" stopColor="var(--brass)" stopOpacity="0" />
            </linearGradient>
          </defs>
          {gridLines.map((y) => (
            <line
              key={y}
              x1="0"
              x2="100"
              y1={y}
              y2={y}
              stroke="var(--border)"
              strokeWidth="0.4"
              vectorEffect="non-scaling-stroke"
            />
          ))}
          <polygon points={areaPolygon} fill="url(#trend-area-fill)" />
          <polyline
            points={feedbackPolyline}
            fill="none"
            stroke="var(--foreground)"
            strokeWidth="1.5"
            vectorEffect="non-scaling-stroke"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
          <polyline
            points={severityPolyline}
            fill="none"
            stroke="var(--warning)"
            strokeWidth="1.1"
            strokeDasharray="2.5 2.5"
            vectorEffect="non-scaling-stroke"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
          {/* the one deliberate cyan touch — "this is live, as of now" */}
          <circle cx={latest.x} cy={latest.y} r="1.4" fill="var(--chart-2)" vectorEffect="non-scaling-stroke" />
        </svg>

        {/* hover targets — HTML overlay positioned at each point's (x%, y%) */}
        {points.map((p, i) => {
          const { x, y } = toXY(i, p.feedback_count);
          return (
            <div
              key={p.date}
              className="group absolute h-3 w-3 -translate-x-1/2 -translate-y-1/2 cursor-default"
              style={{ left: `${x}%`, top: `${y}%` }}
            >
              <div className="h-full w-full rounded-full bg-transparent group-hover:bg-foreground/10" />
              <div
                role="tooltip"
                className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-1.5 -translate-x-1/2 whitespace-nowrap border border-border bg-popover px-2.5 py-1.5 text-xs text-popover-foreground opacity-0 shadow-md transition-opacity group-hover:opacity-100"
              >
                <p className="font-medium">{formatShortDate(p.date)}</p>
                <p className="font-mono text-[10px] text-muted-foreground">
                  {p.feedback_count} feedback · {p.high_severity_count} high severity
                </p>
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-2 flex justify-between text-[11px] text-muted-foreground">
        <span>{formatShortDate(points[0].date)}</span>
        <span>{formatShortDate(points[points.length - 1].date)}</span>
      </div>
    </div>
  );
}

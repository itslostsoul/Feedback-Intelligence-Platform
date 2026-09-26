"use client";

import type { LucideIcon } from "lucide-react";
import { cn } from "cn";

export interface PipelineStage {
  key: string;
  label: string;
  icon: LucideIcon;
  /** Omit to render a non-interactive (informational) stage. */
  onClick?: () => void;
  /** This stage's data is loaded/available right now. */
  active?: boolean;
  /** Optional short caption shown under the label on wide screens. */
  hint?: string;
}

/**
 * Horizontal pipeline visualization: RAW SIGNALS -> PRODUCT ISSUES ->
 * ANALYTICS (dashboard), or the per-issue investigation stages inside the
 * drawer. Every clickable stage wires to a real, already-existing action in
 * the page that renders it — this component never invents navigation or
 * state of its own. Mono/uppercase labels here are a deliberate exception to
 * the "use mono sparingly" rule elsewhere — this is literally system/pipeline
 * status, the one case mono is meant for.
 */
export default function PipelineStepper({
  stages,
  className,
}: {
  stages: PipelineStage[];
  className?: string;
}) {
  return (
    <ol className={cn("flex min-w-max items-stretch", className)}>
      {stages.map((stage, i) => {
        const Icon = stage.icon;
        const interactive = !!stage.onClick;
        return (
          <li key={stage.key} className="flex items-center">
            <button
              type="button"
              onClick={stage.onClick}
              disabled={!interactive}
              title={stage.hint}
              className={cn(
                "group flex items-center gap-2.5 rounded px-2.5 py-2 text-left transition-colors",
                interactive ? "cursor-pointer hover:bg-foreground/[0.04]" : "cursor-default opacity-60"
              )}
            >
              <span
                className={cn(
                  "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border transition-all",
                  stage.active
                    ? "border-brass/50 bg-brass/15 text-brass"
                    : "border-border bg-transparent text-muted-foreground/70"
                )}
              >
                <Icon className="h-3.5 w-3.5" strokeWidth={2} />
              </span>
              <span
                className={cn(
                  "whitespace-nowrap font-mono text-[10px] uppercase tracking-wider",
                  stage.active ? "text-brass" : "text-muted-foreground",
                  interactive && "group-hover:text-foreground"
                )}
              >
                {stage.label}
              </span>
            </button>
            {i < stages.length - 1 && (
              <span
                className="mx-0.5 h-px w-4 shrink-0 bg-border sm:w-8"
                aria-hidden="true"
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}

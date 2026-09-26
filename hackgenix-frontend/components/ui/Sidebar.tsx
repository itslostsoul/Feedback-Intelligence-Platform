"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Search, PanelLeftClose, PanelLeftOpen, Settings } from "lucide-react";
import { cn } from "cn";

const NAV_ITEMS = [
  { index: "01", label: "Overview", href: "/" },
  { index: "02", label: "Raw Firehose", href: "/firehose" },
  { index: "03", label: "AI Analytics", href: "/analytics" },
];

const COLLAPSE_STORAGE_KEY = "signalforge:sidebar-collapsed";

/** An editorial index, not an admin menu — numbered destinations, a thin
 * rule, and a brass underline for the active row instead of icon buttons in
 * filled pills. Only ever links to routes that actually exist. */
export default function Sidebar() {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);

  // Read the persisted preference after mount only, so the server-rendered
  // (always-expanded) markup matches the client's first paint — avoids a
  // hydration mismatch instead of trying to guess the stored value on the
  // server. A lazy useState initializer can't substitute for this: it would
  // run during the client's hydration render too, reading localStorage
  // before the server-rendered (expanded) markup has been reconciled and
  // reintroducing the exact mismatch this effect exists to avoid.
  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(COLLAPSE_STORAGE_KEY) === "1";
      // eslint-disable-next-line react-hooks/set-state-in-effect -- syncing from an external, client-only source (localStorage) after mount; see comment above.
      setCollapsed(stored);
    } catch {
      // Storage unavailable (private mode, etc.) — expanded is a fine default.
    }
  }, []);

  function toggleCollapsed() {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(COLLAPSE_STORAGE_KEY, next ? "1" : "0");
      } catch {
        // Non-fatal — the preference just won't persist across reloads.
      }
      return next;
    });
  }

  return (
    <aside
      className={cn(
        "sticky top-0 hidden h-screen shrink-0 flex-col border-r border-sidebar-border bg-sidebar py-6 transition-[width] duration-200 md:flex",
        collapsed ? "w-16 px-3" : "w-60 px-6"
      )}
    >
      <div className={cn("flex items-center", collapsed ? "flex-col gap-3" : "justify-between")}>
        <div className={cn("flex items-center gap-2", collapsed && "flex-col")}>
          <span className="relative flex h-1.5 w-1.5 shrink-0">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-chart-2 opacity-50" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-chart-2" />
          </span>
          {!collapsed && <span className="text-[11px] text-muted-foreground">Live</span>}
        </div>
        <button
          onClick={toggleCollapsed}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-muted-foreground/50 transition-colors hover:text-foreground"
        >
          {collapsed ? <PanelLeftOpen className="h-3.5 w-3.5" /> : <PanelLeftClose className="h-3.5 w-3.5" />}
        </button>
      </div>

      <div
        className={cn(
          "mt-6 font-serif tracking-tight text-sidebar-foreground",
          collapsed ? "mb-8 text-center text-base" : "mb-10 text-xl"
        )}
      >
        {collapsed ? "SF" : "SignalForge"}
      </div>

      <nav className={collapsed ? "space-y-4" : ""}>
        {NAV_ITEMS.map(({ index, label, href }) => {
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              title={collapsed ? label : undefined}
              className={cn(
                "group relative block border-b border-sidebar-border/70 py-3 transition-colors last:border-b-0",
                collapsed && "text-center"
              )}
            >
              <span
                className={cn(
                  "flex items-baseline gap-2.5",
                  collapsed && "flex-col items-center gap-1"
                )}
              >
                <span
                  className={cn(
                    "font-mono text-[10px] tabular-nums",
                    active ? "text-brass" : "text-muted-foreground/50 group-hover:text-muted-foreground"
                  )}
                >
                  {index}
                </span>
                {!collapsed && (
                  <span
                    className={cn(
                      "text-sm transition-colors",
                      active ? "font-medium text-foreground" : "text-muted-foreground group-hover:text-foreground"
                    )}
                  >
                    {label}
                  </span>
                )}
              </span>
              {active && (
                <span
                  className="absolute -bottom-px left-0 h-px w-full bg-brass"
                  aria-hidden="true"
                />
              )}
            </Link>
          );
        })}
      </nav>

      <div className="mt-8 flex-1" />

      {/* Not a nav link — Evidence/Decisions/Stability have no standalone
          route (they're an issue's own investigation workspace), so this is
          deliberately a contextual pointer, never a fake page. */}
      {!collapsed && (
        <div className="mb-6 border-t border-sidebar-border/70 pt-4">
          <p className="mb-1.5 flex items-center gap-1.5 text-[11px] text-muted-foreground/70">
            <Search className="h-3 w-3" /> Investigate
          </p>
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            Open any product issue to see its Evidence, Decision, and Challenge / Stability workspace.
          </p>
        </div>
      )}

      {/* Static identity footer — no settings/profile backend exists, so this
          is deliberately non-interactive rather than a dead-looking button.
          "MindFlayers" is the team credit, kept subordinate to the
          SignalForge product wordmark above. */}
      <div
        className={cn(
          "flex items-center border-t border-sidebar-border/70 pt-4 text-sm text-muted-foreground",
          collapsed ? "flex-col gap-3" : "justify-between"
        )}
      >
        <div className={cn("flex items-center gap-2.5", collapsed && "flex-col")}>
          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-sidebar-accent font-mono text-[10px] text-sidebar-foreground">
            N
          </div>
          {!collapsed && (
            <div className="leading-tight">
              <p className="font-medium text-sidebar-foreground">Nimal YD</p>
              <p className="text-[10px] uppercase tracking-wide text-muted-foreground/60">by MindFlayers</p>
            </div>
          )}
        </div>
        {!collapsed && <Settings className="h-4 w-4 opacity-40" aria-hidden="true" />}
      </div>
    </aside>
  );
}

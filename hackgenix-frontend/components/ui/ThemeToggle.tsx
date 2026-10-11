"use client";

import { Moon, Sun } from "lucide-react";
import { cn } from "cn";
import { useTheme } from "@/components/ui/ThemeProvider";

/** A real, labeled, keyboard-accessible control — never an icon alone on
 * desktop; `iconOnly` keeps it accessible (aria-label + title still
 * describe the action) while saving space in MobileNav's compact bar.
 * Placed in the shared layout (desktop content header + MobileNav) rather
 * than duplicated per page. */
export function ThemeToggle({ className, iconOnly }: { className?: string; iconOnly?: boolean }) {
  const { theme, setTheme } = useTheme();
  const isDark = theme === "dark";

  return (
    <button
      type="button"
      onClick={() => setTheme(isDark ? "light" : "dark")}
      aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
      title={isDark ? "Switch to light theme" : "Switch to dark theme"}
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 border border-border bg-surface-2 px-2.5 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:border-brass hover:text-brass focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        className
      )}
    >
      {isDark ? <Moon className="h-3.5 w-3.5" strokeWidth={2} /> : <Sun className="h-3.5 w-3.5" strokeWidth={2} />}
      {!iconOnly && (isDark ? "Dark" : "Light")}
    </button>
  );
}

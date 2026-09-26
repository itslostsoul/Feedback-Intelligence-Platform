"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, Inbox, LineChart } from "lucide-react";

const NAV_ITEMS = [
  { label: "Overview", icon: LayoutDashboard, href: "/" },
  { label: "Raw Firehose", icon: Inbox, href: "/firehose" },
  { label: "AI Analytics", icon: LineChart, href: "/analytics" },
];

/** Compact top bar shown only below md — Sidebar carries navigation above
 * that. Icon-only (with aria-label + title) so three destinations still fit
 * comfortably next to the wordmark on a narrow viewport. */
export default function MobileNav() {
  const pathname = usePathname();

  return (
    <header className="flex items-center justify-between border-b border-sidebar-border bg-sidebar px-4 py-3 md:hidden">
      <div className="font-serif text-lg tracking-tight text-sidebar-foreground">SignalForge</div>
      <nav className="flex items-center gap-1">
        {NAV_ITEMS.map(({ label, icon: Icon, href }) => {
          const active = pathname === href;
          return (
            <Link
              key={label}
              href={href}
              aria-label={label}
              title={label}
              className={
                active
                  ? "flex items-center justify-center rounded p-2 text-brass"
                  : "flex items-center justify-center rounded p-2 text-muted-foreground hover:text-foreground"
              }
            >
              <Icon className="h-4 w-4" strokeWidth={2} />
            </Link>
          );
        })}
      </nav>
    </header>
  );
}

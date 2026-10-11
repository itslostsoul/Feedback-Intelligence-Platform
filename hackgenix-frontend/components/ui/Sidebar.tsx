"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, Inbox, LineChart, Settings } from "lucide-react";

const NAV_ITEMS = [
  { label: "Impact Grid", icon: LayoutDashboard, href: "/" },
  { label: "Raw Firehose", icon: Inbox, href: "/firehose" },
  { label: "AI Analytics", icon: LineChart, href: null }, // no backend aggregate endpoint yet
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="hidden md:flex h-screen w-64 flex-col border-r border-sidebar-border bg-sidebar p-6">
      <div className="mb-1 flex items-center gap-2">
        <span className="relative flex h-1.5 w-1.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-60" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-primary" />
        </span>
        <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          Live
        </span>
      </div>

      <div className="mb-10 mt-3 font-mono text-xl font-semibold tracking-tight text-sidebar-foreground">
        Mind<span className="text-primary">Flayers</span>
      </div>

      <nav className="flex-1 space-y-1">
        {NAV_ITEMS.map(({ label, icon: Icon, href }) => {
          const active = href !== null && pathname === href;
          const baseClasses = "flex items-center gap-3 rounded-lg px-4 py-2.5 text-sm font-medium transition-colors";

          if (href === null) {
            return (
              <div
                key={label}
                className={`${baseClasses} cursor-not-allowed text-muted-foreground/50`}
                title="Coming soon"
              >
                <Icon className="h-4 w-4" strokeWidth={2} />
                {label}
                <span className="ml-auto font-mono text-[9px] uppercase tracking-wider">Soon</span>
              </div>
            );
          }

          return (
            <Link
              key={label}
              href={href}
              className={
                active
                  ? `${baseClasses} bg-sidebar-accent text-primary`
                  : `${baseClasses} text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground`
              }
            >
              <Icon className="h-4 w-4" strokeWidth={2} />
              {label}
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto flex cursor-pointer items-center justify-between border-t border-sidebar-border pt-5 text-sm text-muted-foreground transition-colors hover:text-sidebar-foreground">
        <div className="flex items-center gap-2.5">
          <div className="flex h-6 w-6 items-center justify-center rounded-full bg-sidebar-accent font-mono text-[10px] text-sidebar-foreground">
            N
          </div>
          <span className="font-medium">Nimal YD</span>
        </div>
        <Settings className="h-4 w-4" />
      </div>
    </aside>
  );
}

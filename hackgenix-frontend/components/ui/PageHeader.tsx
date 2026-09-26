import type { ReactNode } from "react";

/**
 * Adaptive page header for routes that don't need a fully bespoke hero
 * (currently just Raw Firehose — Overview and AI Analytics each have their
 * own distinct header treatment, since the whole point is that they read as
 * different modes of the same product, not the same header three times).
 */
export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-10 flex flex-wrap items-end justify-between gap-4 border-b border-border pb-6">
      <div>
        {eyebrow && (
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-brass">{eyebrow}</p>
        )}
        <h1 className="font-serif text-3xl tracking-tight text-foreground md:text-4xl">{title}</h1>
        {description && (
          <p className="mt-2 max-w-[58ch] text-sm leading-relaxed text-muted-foreground">{description}</p>
        )}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2.5">{actions}</div>}
    </div>
  );
}

/** Small section label used to open an editorial block — no card, no border
 * box, just a marker before the content it introduces. */
export function SectionLabel({ children, accent }: { children: ReactNode; accent?: string }) {
  return (
    <p className={`mb-4 text-xs font-medium uppercase tracking-wide ${accent ?? "text-muted-foreground"}`}>
      {children}
    </p>
  );
}

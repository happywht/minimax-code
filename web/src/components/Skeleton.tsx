/**
 * Reusable skeleton/shimmer placeholder components.
 *
 * Used during data loading states to provide visual feedback instead
 * of plain "Loading…" text. Each variant matches a common UI pattern:
 *
 * - `<SkeletonLine />` — generic shimmer bar (table row, list item, etc.)
 * - `<SkeletonCircle />` — avatar or icon placeholder
 * - `<SkeletonCard />` — card-like block with header + 2 lines
 * - `<SkeletonTable rows={N} />` — table with N rows of varying widths
 *
 * All variants share the `animate-shimmer` CSS animation (gradient sweep)
 * defined in `index.css` for a premium loading feel.
 */
interface SkeletonBaseProps {
  className?: string;
}

export function SkeletonLine({ className }: SkeletonBaseProps): JSX.Element {
  return (
    <div
      className={`h-4 rounded animate-shimmer ${className ?? ""}`}
    />
  );
}

export function SkeletonCircle({ className }: SkeletonBaseProps): JSX.Element {
  return (
    <div
      className={`h-8 w-8 rounded-full animate-shimmer shrink-0 ${className ?? ""}`}
    />
  );
}

interface SkeletonCardProps extends SkeletonBaseProps {
  lines?: number;
}

export function SkeletonCard({ className, lines = 2 }: SkeletonCardProps): JSX.Element {
  return (
    <div className={`rounded-lg border border-minimax-border bg-minimax-panel p-4 space-y-3 ${className ?? ""}`}>
      <SkeletonLine className="h-5 w-2/3" />
      {Array.from({ length: lines }, (_, i) => (
        <SkeletonLine key={i} className={i === lines - 1 ? "w-1/2" : "w-full"} />
      ))}
    </div>
  );
}

interface SkeletonTableProps extends SkeletonBaseProps {
  rows?: number;
}

export function SkeletonTable({ className, rows = 5 }: SkeletonTableProps): JSX.Element {
  // Varying widths for a natural look
  const widths = ["w-1/4", "w-1/3", "w-1/2", "w-2/5", "w-3/5"];
  return (
    <div className={`space-y-2 ${className ?? ""}`}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-3 rounded-md border border-minimax-border/40 px-3 py-2.5">
          <SkeletonLine className="w-1/4" />
          <SkeletonLine className={widths[i % widths.length]} />
          <SkeletonLine className="w-1/6 ml-auto" />
        </div>
      ))}
    </div>
  );
}

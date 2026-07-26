/**
 * PanelFallback — Suspense placeholder while a lazily-loaded
 * inspector panel chunk is on the wire.
 */
import { Spinner } from "../../ui";

export function PanelFallback(): JSX.Element {
  return (
    <div className="flex min-h-24 items-center justify-center" aria-busy="true">
      <Spinner size={16} />
    </div>
  );
}

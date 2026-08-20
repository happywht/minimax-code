/**
 * CollapsedStrip — the thin rail shown when the whole right panel is
 * collapsed. Only the expand affordance remains visible.
 */
import { ChevronLeft } from "lucide-react";
import { IconButton } from "../../ui";
import { strings } from "../../ui/strings";

export interface CollapsedStripProps {
  testId: string;
  onExpand: () => void;
}

export function CollapsedStrip({ testId, onExpand }: CollapsedStripProps): JSX.Element {
  return (
    <div
      data-testid={`${testId}-collapsed`}
      className="flex h-full w-8 shrink-0 flex-col items-center border-l border-line bg-surface-1 transition-all duration-200"
    >
      <IconButton
        aria-label={strings.rightPanel.shell.expand}
        data-testid={`${testId}-expand`}
        onClick={onExpand}
        className="mt-3"
      >
        <ChevronLeft />
      </IconButton>
    </div>
  );
}

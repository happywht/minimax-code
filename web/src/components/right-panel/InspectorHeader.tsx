/**
 * InspectorHeader — panel chrome: the "Inspector" eyebrow, the
 * currently active tab (icon + label), and the collapse action.
 */
import { ChevronRight } from "lucide-react";
import { IconButton } from "../../ui";
import { tabMeta, type InspectorTab } from "./tabs";

export interface InspectorHeaderProps {
  testId: string;
  activeTab: InspectorTab;
  onCollapse: () => void;
}

export function InspectorHeader({
  testId,
  activeTab,
  onCollapse,
}: InspectorHeaderProps): JSX.Element {
  const meta = tabMeta(activeTab);
  return (
    <header className="flex items-center justify-between border-b border-line px-3 py-2">
      <div className="min-w-0">
        <span className="block text-[11px] font-semibold uppercase tracking-wider text-ink-2">
          Inspector
        </span>
        <span className="mt-0.5 flex items-center gap-1.5 truncate text-xs text-ink-1">
          <span className="flex items-center text-accent">{meta.icon}</span>
          <span className="truncate">{meta.label}</span>
        </span>
      </div>
      <IconButton
        aria-label="Collapse right panel"
        title="Collapse right panel"
        size="sm"
        data-testid={`${testId}-collapse`}
        onClick={onCollapse}
      >
        <ChevronRight />
      </IconButton>
    </header>
  );
}

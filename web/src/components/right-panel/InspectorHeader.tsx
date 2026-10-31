/**
 * InspectorHeader — panel chrome: the "Inspector" eyebrow, the
 * currently active tab (icon + label), and the collapse action.
 */
import { ChevronRight } from "lucide-react";
import { IconButton } from "../../ui";
import { strings } from "../../ui/strings";
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
      <div className="min-w-0" title={meta.label}>
        <span className="block text-xs font-medium text-ink-1">
          {strings.rightPanel.shell.inspector}
        </span>
      </div>
      <IconButton
        aria-label={strings.rightPanel.shell.collapse}
        title={strings.rightPanel.shell.collapse}
        size="sm"
        data-testid={`${testId}-collapse`}
        onClick={onCollapse}
      >
        <ChevronRight />
      </IconButton>
    </header>
  );
}

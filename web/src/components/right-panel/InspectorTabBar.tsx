/**
 * InspectorTabBar — horizontally scrollable icon tab strip. The
 * active tab is highlighted with the accent-subtle treatment; every
 * tab keeps an accessible name even though only the icon is shown.
 */
import { IconButton } from "../../ui";
import { INSPECTOR_TABS, type InspectorTab } from "./tabs";

export interface InspectorTabBarProps {
  testId: string;
  activeTab: InspectorTab;
  onSelect: (tab: InspectorTab) => void;
}

export function InspectorTabBar({
  testId,
  activeTab,
  onSelect,
}: InspectorTabBarProps): JSX.Element {
  return (
    <div
      role="tablist"
      aria-label="Inspector panels"
      className="flex gap-1 overflow-x-auto border-b border-line px-2 py-1.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
    >
      {INSPECTOR_TABS.map((tab) => {
        const selected = activeTab === tab.id;
        return (
          <IconButton
            key={tab.id}
            role="tab"
            aria-label={tab.label}
            aria-selected={selected}
            aria-controls={`${testId}-${tab.id}-panel`}
            data-testid={`${testId}-tab-${tab.id}`}
            title={tab.label}
            active={selected}
            onClick={() => onSelect(tab.id)}
            className="h-8 min-w-8 px-2"
          >
            {tab.icon}
          </IconButton>
        );
      })}
    </div>
  );
}

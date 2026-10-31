import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { strings } from "../../ui/strings";
import { INSPECTOR_TABS, type InspectorTab } from "./tabs";

export interface InspectorTabBarProps {
  testId: string;
  activeTab: InspectorTab;
  onSelect: (tab: InspectorTab) => void;
}

const PRIMARY_TABS: InspectorTab[] = ["timeline", "diff", "progress"];

export function InspectorTabBar({ testId, activeTab, onSelect }: InspectorTabBarProps): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  // Keep a selected secondary panel visible even after closing the list.
  const visibleTabs = INSPECTOR_TABS.filter((tab) => expanded || PRIMARY_TABS.includes(tab.id) || tab.id === activeTab);
  return (
    <div className="border-b border-line px-2 py-2">
      <div role="tablist" aria-label={strings.rightPanel.shell.panelsAria} className="grid grid-cols-2 gap-1">
        {visibleTabs.map((tab) => {
          const selected = activeTab === tab.id;
          return (
            <button type="button" key={tab.id} role="tab" aria-label={tab.label}
              aria-selected={selected} aria-controls={`${testId}-${tab.id}-panel`}
              data-testid={`${testId}-tab-${tab.id}`} title={tab.label} tabIndex={selected ? 0 : -1}
              onClick={() => onSelect(tab.id)}
              onKeyDown={(event) => {
                const index = visibleTabs.findIndex((item) => item.id === activeTab);
                let next = index;
                if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (index + 1) % visibleTabs.length;
                else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (index - 1 + visibleTabs.length) % visibleTabs.length;
                else if (event.key === "Home") next = 0;
                else if (event.key === "End") next = visibleTabs.length - 1;
                else return;
                event.preventDefault();
                const target = visibleTabs[next];
                onSelect(target.id);
                event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`[data-testid="${testId}-tab-${target.id}"]`)?.focus();
              }}
              className={`flex min-w-0 items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent ${selected ? "bg-accent-subtle font-medium text-ink-0" : "text-ink-1 hover:bg-surface-3"}`}>
              <span className={selected ? "shrink-0 text-accent" : "shrink-0 text-ink-2"}>{tab.icon}</span>
              <span className="truncate">{tab.label}</span>
            </button>
          );
        })}
      </div>
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}
        className="mt-1 flex w-full items-center gap-1 rounded-md px-2 py-1 text-left text-xs text-ink-2 hover:bg-surface-3 hover:text-ink-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />} 更多面板
      </button>
    </div>
  );
}

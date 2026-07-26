/**
 * RightPanel — third column of the three-pane shell.
 *
 * A thin composition layer over the pieces in ``./right-panel/``:
 *
 *   - ``InspectorHeader``   — chrome: title, active tab, collapse
 *   - ``InspectorTabBar``   — icon tab strip across all panels
 *   - ``FollowRunBar``      — shown while a tab is manually pinned
 *   - ``InspectorContent``  — one tabpanel per tab (lazy chunks)
 *   - ``CollapsedStrip``    — the rail shown when fully collapsed
 *
 * State lives in two hooks: ``useInspectorFollow`` (active tab +
 * auto-follow of run activity) and ``useAgentTeam`` (sub-agent
 * roster loading). All building blocks use the shared design-system
 * primitives from ``src/ui``.
 */
import { useState } from "react";
import type { AgentInfo } from "../types/ipc";
import { CollapsedStrip } from "./right-panel/CollapsedStrip";
import { FollowRunBar } from "./right-panel/FollowRunBar";
import { InspectorContent } from "./right-panel/InspectorContent";
import { InspectorHeader } from "./right-panel/InspectorHeader";
import { InspectorTabBar } from "./right-panel/InspectorTabBar";
import type { InspectorTab } from "./right-panel/tabs";
import { useAgentTeam } from "./right-panel/useAgentTeam";
import { useInspectorFollow } from "./right-panel/useInspectorFollow";

export interface RightPanelProps {
  testId?: string;
  /** When true, render the panel pre-collapsed. Mainly for tests. */
  defaultCollapsed?: boolean;
  /** Initial inspector tab. Mainly for tests and future deep links. */
  defaultTab?: InspectorTab;
  /** Override agent list for tests. */
  initialAgents?: AgentInfo[];
  /** Override the IPC listAgents call for tests. */
  loadAgents?: () => Promise<AgentInfo[]>;
}

export function RightPanel({
  testId = "right-panel",
  defaultCollapsed = false,
  defaultTab = "timeline",
  initialAgents,
  loadAgents,
}: RightPanelProps): JSX.Element {
  const [collapsed, setCollapsed] = useState<boolean>(defaultCollapsed);
  const { activeTab, followRun, selectTab, resumeFollow } = useInspectorFollow(defaultTab);
  const { agents, agentsLoading, agentsError, fetchAgents } = useAgentTeam(
    initialAgents,
    loadAgents,
  );

  if (collapsed) {
    return <CollapsedStrip testId={testId} onExpand={() => setCollapsed(false)} />;
  }

  return (
    <aside
      data-testid={testId}
      className="flex h-full w-64 shrink-0 flex-col border-l border-line bg-surface-1 transition-all duration-200 xl:w-[280px]"
    >
      <InspectorHeader
        testId={testId}
        activeTab={activeTab}
        onCollapse={() => setCollapsed(true)}
      />
      <InspectorTabBar testId={testId} activeTab={activeTab} onSelect={selectTab} />
      {!followRun ? <FollowRunBar testId={testId} onResume={resumeFollow} /> : null}
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        <InspectorContent
          activeTab={activeTab}
          agents={agents}
          agentsLoading={agentsLoading}
          agentsError={agentsError}
          fetchAgents={fetchAgents}
          testId={testId}
        />
      </div>
    </aside>
  );
}

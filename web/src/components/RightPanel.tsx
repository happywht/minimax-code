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
import { Lightbulb, X } from "lucide-react";
import { strings } from "../ui/strings";
import type { AgentInfo } from "../types/ipc";
import { CollapsedStrip } from "./right-panel/CollapsedStrip";
import { FollowRunBar } from "./right-panel/FollowRunBar";
import { InspectorContent } from "./right-panel/InspectorContent";
import { InspectorHeader } from "./right-panel/InspectorHeader";
import { InspectorTabBar } from "./right-panel/InspectorTabBar";
import type { InspectorTab } from "./right-panel/tabs";
import { useAgentTeam } from "./right-panel/useAgentTeam";
import { useInspectorFollow } from "./right-panel/useInspectorFollow";

/** localStorage flag suppressing the first-run inspector tab hint. */
const HINT_SEEN_KEY = "minimax_inspector_hint_seen";

/**
 * One-time discoverability hint under the tab strip: the 11 icon-only
 * tabs have title/aria labels but nothing announces the strip itself on
 * first sight (v1.7.1 walkthrough finding). Dismissed once, never again.
 */
function InspectorHint(): JSX.Element | null {
  const [seen, setSeen] = useState<boolean>(() => {
    try {
      return localStorage.getItem(HINT_SEEN_KEY) === "1";
    } catch {
      return false;
    }
  });

  if (seen) return null;

  const dismiss = () => {
    try {
      localStorage.setItem(HINT_SEEN_KEY, "1");
    } catch {
      /* private mode — hint just returns for this session */
    }
    setSeen(true);
  };

  return (
    <div
      data-testid="inspector-tab-hint"
      className="flex items-start gap-1.5 border-b border-line/60 bg-surface-2/60 px-2 py-1.5"
    >
      <Lightbulb size={11} aria-hidden="true" className="mt-0.5 shrink-0 text-status-warning" />
      <p className="min-w-0 flex-1 text-[11px] leading-4 text-ink-2">
        {strings.rightPanel.shell.hint.text}
      </p>
      <button
        type="button"
        onClick={dismiss}
        aria-label={strings.rightPanel.shell.hint.dismissAria}
        data-testid="inspector-tab-hint-dismiss"
        className="shrink-0 rounded p-0.5 text-ink-3 hover:bg-surface-3 hover:text-ink-0"
      >
        <X size={10} aria-hidden="true" />
      </button>
    </div>
  );
}

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
      <InspectorHint />
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

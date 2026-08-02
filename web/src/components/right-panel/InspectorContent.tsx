/**
 * InspectorContent — tabpanel switch: renders exactly one inspector
 * panel for the active tab. Heavy panels are lazy-loaded so the
 * initial bundle only pays for the timeline view.
 */
import { lazy, Suspense } from "react";
import { RunTimelinePanel } from "./RunTimelinePanel";
import type { AgentInfo } from "../../types/ipc";
import { AgentTeamPanel } from "./AgentTeamPanel";
import { PanelFallback } from "./PanelFallback";
import type { InspectorTab } from "./tabs";

const CodeReviewPanel = lazy(() =>
  import("../panels/CodeReviewPanel").then((module) => ({ default: module.CodeReviewPanel })),
);
const PatchPreviewPanel = lazy(() =>
  import("../panels/PatchPreviewPanel").then((module) => ({ default: module.PatchPreviewPanel })),
);
const ProgressPanel = lazy(() =>
  import("./ProgressPanel").then((module) => ({ default: module.ProgressPanel })),
);
const RunnerPanel = lazy(() =>
  import("./RunnerPanel").then((module) => ({ default: module.RunnerPanel })),
);
const SubAgentPanel = lazy(() =>
  import("./SubAgentPanel").then((module) => ({ default: module.SubAgentPanel })),
);
const TeamRunPanel = lazy(() =>
  import("./TeamRunPanel").then((module) => ({ default: module.TeamRunPanel })),
);
const TerminalPanel = lazy(() =>
  import("./TerminalPanel").then((module) => ({ default: module.TerminalPanel })),
);
const CodebasePanel = lazy(() =>
  import("./CodebasePanel").then((module) => ({ default: module.CodebasePanel })),
);
const CheckpointPanel = lazy(() =>
  import("./CheckpointPanel").then((module) => ({ default: module.CheckpointPanel })),
);

export interface InspectorContentProps {
  activeTab: InspectorTab;
  agents: AgentInfo[] | null;
  agentsLoading: boolean;
  agentsError: string | null;
  fetchAgents: () => Promise<void>;
  testId: string;
}

export function InspectorContent({
  activeTab,
  agents,
  agentsLoading,
  agentsError,
  fetchAgents,
  testId,
}: InspectorContentProps): JSX.Element {
  switch (activeTab) {
    case "timeline":
      return (
        <section id={`${testId}-timeline-panel`} role="tabpanel" data-testid={`${testId}-timeline-body`}>
          <RunTimelinePanel testId={`${testId}-timeline-panel`} />
        </section>
      );
    case "diff":
      return (
        <section id={`${testId}-diff-panel`} role="tabpanel" data-testid={`${testId}-patch-body`}>
          <Suspense fallback={<PanelFallback />}>
            <PatchPreviewPanel testId={`${testId}-patch-panel`} />
          </Suspense>
        </section>
      );
    case "progress":
      return (
        <section id={`${testId}-progress-panel`} role="tabpanel" data-testid={`${testId}-progress-body`}>
          <Suspense fallback={<PanelFallback />}>
            <ProgressPanel testId={`${testId}-progress-panel`} />
          </Suspense>
        </section>
      );
    case "checkpoints":
      return (
        <section id={`${testId}-checkpoints-panel`} role="tabpanel" data-testid={`${testId}-checkpoints-body`}>
          <Suspense fallback={<PanelFallback />}>
            <CheckpointPanel testId={`${testId}-checkpoints-panel`} />
          </Suspense>
        </section>
      );
    case "agents":
      return (
        <AgentTeamPanel
          testId={testId}
          agents={agents}
          loading={agentsLoading}
          error={agentsError}
          onRefresh={() => void fetchAgents()}
        />
      );
    case "subagents":
      return (
        <section id={`${testId}-subagents-panel`} role="tabpanel" data-testid={`${testId}-sub-body`}>
          <Suspense fallback={<PanelFallback />}>
            <SubAgentPanel testId={`${testId}-sub-panel`} />
          </Suspense>
        </section>
      );
    case "review":
      return (
        <section id={`${testId}-review-panel`} role="tabpanel" data-testid={`${testId}-review-body`}>
          <Suspense fallback={<PanelFallback />}>
            <CodeReviewPanel testId={`${testId}-review-panel`} />
          </Suspense>
        </section>
      );
    case "terminal":
      return (
        <section id={`${testId}-terminal-panel`} role="tabpanel" data-testid={`${testId}-terminal-body`}>
          <Suspense fallback={<PanelFallback />}>
            <TerminalPanel testId={`${testId}-terminal-panel`} />
          </Suspense>
        </section>
      );
    case "runner":
      return (
        <section id={`${testId}-runner-panel`} role="tabpanel" data-testid={`${testId}-runner-body`}>
          <Suspense fallback={<PanelFallback />}>
            <RunnerPanel testId={`${testId}-runner-panel`} />
          </Suspense>
        </section>
      );
    case "teamruns":
      return (
        <section id={`${testId}-teamruns-panel`} role="tabpanel" data-testid={`${testId}-teamrun-body`}>
          <Suspense fallback={<PanelFallback />}>
            <TeamRunPanel />
          </Suspense>
        </section>
      );
    case "codebase":
      return (
        <section id={`${testId}-codebase-panel`} role="tabpanel" data-testid={`${testId}-codebase-body`}>
          <Suspense fallback={<PanelFallback />}>
            <CodebasePanel testId={`${testId}-codebase-panel`} />
          </Suspense>
        </section>
      );
  }
}

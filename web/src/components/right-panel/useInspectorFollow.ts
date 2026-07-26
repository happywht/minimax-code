/**
 * useInspectorFollow — "follow the run" behaviour for the inspector.
 *
 * While `followRun` is true, activity in any of the tracked stores
 * (timeline / sub-agents / tasks / permissions / diff / terminal /
 * runner) auto-switches the active tab to the panel that can explain
 * what is happening. As soon as the user clicks a tab manually the
 * follow mode is pinned off until they press "Follow run" again.
 */
import { useCallback, useEffect, useState } from "react";
import {
  usePatchPreviewStore,
  usePermissionStore,
  useRunnerStore,
  useRunTimelineStore,
  useSubAgentStore,
  useTaskStore,
  useTerminalStore,
} from "../../stores";
import type { InspectorTab } from "./tabs";

export interface InspectorFollow {
  activeTab: InspectorTab;
  followRun: boolean;
  /** Pin a tab manually (disables follow mode). */
  selectTab: (tab: InspectorTab) => void;
  /** Re-enable follow mode. */
  resumeFollow: () => void;
}

export function useInspectorFollow(defaultTab: InspectorTab): InspectorFollow {
  const [activeTab, setActiveTab] = useState<InspectorTab>(defaultTab);
  const [followRun, setFollowRun] = useState<boolean>(true);

  const timelineSignal = useRunTimelineStore((s) => {
    const stepCount = Object.values(s.runs).reduce((sum, run) => sum + run.steps.length, 0);
    return `${s.order.length}:${stepCount}`;
  });
  const subAgentSignal = useSubAgentStore((s) => {
    const runs = Object.values(s.runs);
    const latest = runs.reduce((max, run) => Math.max(max, run.updated_at), 0);
    return `${runs.length}:${latest}`;
  });
  const taskSignal = useTaskStore((s) => {
    const tasks = Object.values(s.tasks);
    const latest = tasks.reduce((max, task) => Math.max(max, task.updated_at), 0);
    return `${tasks.length}:${latest}`;
  });
  const permissionSignal = usePermissionStore((s) => `${s.pending.length}`);
  const diffSignal = usePatchPreviewStore((s) => `${s.result?.files?.length ?? 0}`);
  const terminalSignal = useTerminalStore((s) => {
    const sessions = Object.values(s.sessions);
    const running = sessions.filter(
      (session) => session.status === "running" || session.status === "starting",
    ).length;
    const latest = sessions.reduce((max, session) => Math.max(max, session.updated_at), 0);
    return `${running}:${latest}`;
  });
  const runnerSignal = useRunnerStore(
    (s) => `${s.lastStart?.session.id ?? ""}:${s.starting ? 1 : 0}`,
  );

  useEffect(() => {
    if (!followRun || timelineSignal === "0:0") return;
    setActiveTab("timeline");
  }, [followRun, timelineSignal]);

  useEffect(() => {
    if (!followRun || subAgentSignal === "0:0") return;
    setActiveTab("subagents");
  }, [followRun, subAgentSignal]);

  useEffect(() => {
    if (!followRun || taskSignal === "0:0") return;
    setActiveTab("progress");
  }, [followRun, taskSignal]);

  useEffect(() => {
    if (!followRun || permissionSignal === "0") return;
    setActiveTab("timeline");
  }, [followRun, permissionSignal]);

  useEffect(() => {
    if (!followRun || diffSignal === "0") return;
    setActiveTab("diff");
  }, [diffSignal, followRun]);

  useEffect(() => {
    if (!followRun || terminalSignal.startsWith("0:")) return;
    setActiveTab("terminal");
  }, [followRun, terminalSignal]);

  useEffect(() => {
    if (!followRun || runnerSignal === ":0") return;
    setActiveTab("runner");
  }, [followRun, runnerSignal]);

  const selectTab = useCallback((tab: InspectorTab) => {
    setActiveTab(tab);
    setFollowRun(false);
  }, []);

  const resumeFollow = useCallback(() => {
    setFollowRun(true);
  }, []);

  return { activeTab, followRun, selectTab, resumeFollow };
}

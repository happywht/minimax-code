/**
 * Inspector tab registry — the single source of truth for which
 * panels live inside the RightPanel, their labels, and their icons.
 */
import {
  Activity,
  Bot,
  Camera,
  CheckCircle2,
  Database,
  GitCompare,
  ListChecks,
  Loader2,
  Rocket,
  TerminalSquare,
  Users,
} from "lucide-react";

import { strings } from "../../ui/strings";

export type InspectorTab =
  | "timeline"
  | "diff"
  | "progress"
  | "checkpoints"
  | "agents"
  | "subagents"
  | "review"
  | "teamruns"
  | "terminal"
  | "runner"
  | "codebase";

export interface InspectorTabMeta {
  id: InspectorTab;
  label: string;
  icon: JSX.Element;
}

export const INSPECTOR_TABS: InspectorTabMeta[] = [
  { id: "timeline", label: strings.rightPanel.tabs.timeline, icon: <Activity size={12} /> },
  { id: "diff", label: strings.rightPanel.tabs.diff, icon: <GitCompare size={12} /> },
  { id: "progress", label: strings.rightPanel.tabs.progress, icon: <ListChecks size={12} /> },
  { id: "checkpoints", label: strings.rightPanel.tabs.checkpoints, icon: <Camera size={12} /> },
  { id: "agents", label: strings.rightPanel.tabs.agents, icon: <Bot size={12} /> },
  { id: "subagents", label: strings.rightPanel.tabs.subagents, icon: <Users size={12} /> },
  { id: "review", label: strings.rightPanel.tabs.review, icon: <CheckCircle2 size={12} /> },
  { id: "teamruns", label: strings.rightPanel.tabs.teamruns, icon: <Loader2 size={12} /> },
  { id: "terminal", label: strings.rightPanel.tabs.terminal, icon: <TerminalSquare size={12} /> },
  { id: "runner", label: strings.rightPanel.tabs.runner, icon: <Rocket size={12} /> },
  { id: "codebase", label: strings.rightPanel.tabs.codebase, icon: <Database size={12} /> },
];

export function tabMeta(tab: InspectorTab): InspectorTabMeta {
  return INSPECTOR_TABS.find((t) => t.id === tab) ?? INSPECTOR_TABS[0];
}

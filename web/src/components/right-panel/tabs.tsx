/**
 * Inspector tab registry — the single source of truth for which
 * panels live inside the RightPanel, their labels, and their icons.
 */
import {
  Activity,
  Bot,
  CheckCircle2,
  Database,
  GitCompare,
  ListChecks,
  Loader2,
  Rocket,
  TerminalSquare,
  Users,
} from "lucide-react";

export type InspectorTab =
  | "timeline"
  | "diff"
  | "progress"
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
  { id: "timeline", label: "Timeline", icon: <Activity size={12} /> },
  { id: "diff", label: "Diff", icon: <GitCompare size={12} /> },
  { id: "progress", label: "Progress", icon: <ListChecks size={12} /> },
  { id: "agents", label: "Agents", icon: <Bot size={12} /> },
  { id: "subagents", label: "Sub", icon: <Users size={12} /> },
  { id: "review", label: "Review", icon: <CheckCircle2 size={12} /> },
  { id: "teamruns", label: "Runs", icon: <Loader2 size={12} /> },
  { id: "terminal", label: "Term", icon: <TerminalSquare size={12} /> },
  { id: "runner", label: "Run", icon: <Rocket size={12} /> },
  { id: "codebase", label: "Code", icon: <Database size={12} /> },
];

export function tabMeta(tab: InspectorTab): InspectorTabMeta {
  return INSPECTOR_TABS.find((t) => t.id === tab) ?? INSPECTOR_TABS[0];
}

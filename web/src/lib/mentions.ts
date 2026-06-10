import type { AgentInfo } from "../types/ipc";

export type MentionKind = "agent" | "file" | "history";

export interface MentionState {
  open: boolean;
  kind: MentionKind;
  query: string;
  cursor: number;
  anchor: number;
}

export interface MentionOption {
  kind: MentionKind;
  id: string;
  label: string;
  detail?: string;
  searchText: string;
  payload: AgentInfo;
}

export const INITIAL_MENTION_STATE: MentionState = {
  open: false,
  kind: "agent",
  query: "",
  cursor: -1,
  anchor: -1,
};

export function detectMentionToken(value: string, caret: number): MentionState {
  const head = value.slice(0, caret);
  const match = /(^|\s)@([\w-]*)$/.exec(head);
  if (!match) return INITIAL_MENTION_STATE;
  return {
    open: true,
    kind: "agent",
    query: match[2],
    cursor: 0,
    anchor: caret - match[2].length - 1,
  };
}

export function agentMentionOptions(agents: AgentInfo[]): MentionOption[] {
  return agents.map((agent) => ({
    kind: "agent",
    id: agent.id,
    label: agent.name,
    detail: agent.description,
    searchText: `${agent.id} ${agent.name}`.toLowerCase(),
    payload: agent,
  }));
}

export function filterMentionOptions(
  options: MentionOption[],
  state: MentionState,
  limit = 6,
): MentionOption[] {
  if (!state.open) return [];
  const query = state.query.toLowerCase();
  return options
    .filter((option) => option.kind === state.kind)
    .filter((option) => !query || option.searchText.includes(query))
    .slice(0, limit);
}

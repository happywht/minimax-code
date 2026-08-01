import type { AgentInfo } from "../types/ipc";

export type MentionKind = "agent" | "repo" | "file";

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
  payload: unknown;
}

export interface MentionContextRequest {
  /** True when the message contains `@repo`. */
  hasRepo: boolean;
  /** Distinct file paths requested via `#path`. */
  files: string[];
  /** The original text with mention markers stripped. */
  cleanText: string;
}

export const INITIAL_MENTION_STATE: MentionState = {
  open: false,
  kind: "agent",
  query: "",
  cursor: -1,
  anchor: -1,
};

/**
 * Detect the mention token at the caret.
 *
 *   - `@repo` or `@rep…` → repo context picker
 *   - `@<other>` → agent picker
 *   - `#<path>` → file picker
 */
export function detectMentionToken(value: string, caret: number): MentionState {
  const head = value.slice(0, caret);

  const repoMatch = /(^|\s)@repo([\w-]*)$/.exec(head);
  if (repoMatch) {
    return {
      open: true,
      kind: "repo",
      query: `repo${repoMatch[2]}`,
      cursor: 0,
      anchor: caret - repoMatch[2].length - 5, // "@repo" prefix
    };
  }

  const agentMatch = /(^|\s)@([\w-]*)$/.exec(head);
  if (agentMatch) {
    return {
      open: true,
      kind: "agent",
      query: agentMatch[2],
      cursor: 0,
      anchor: caret - agentMatch[2].length - 1,
    };
  }

  const fileMatch = /(^|\s)#([\w./-]*)$/.exec(head);
  if (fileMatch) {
    return {
      open: true,
      kind: "file",
      query: fileMatch[2],
      cursor: 0,
      anchor: caret - fileMatch[2].length - 1,
    };
  }

  return INITIAL_MENTION_STATE;
}

/**
 * Extract repo/file context requests from a composed message.
 *
 *   - `@repo` anywhere in the text sets `hasRepo`.
 *   - `#path` tokens are collected as file requests.
 *   - The returned `cleanText` has these markers removed.
 */
export function extractMentionContext(value: string): MentionContextRequest {
  const files: string[] = [];
  const seen = new Set<string>();
  let hasRepo = false;

  const cleaned = value
    .replace(/(^|\s)@repo(\b|\s|$)/g, (match) => {
      hasRepo = true;
      return match.startsWith(" ") ? " " : "";
    })
    .replace(/(^|\s)#([\w./-]+)/g, (_match, leading, path: string) => {
      if (!seen.has(path)) {
        seen.add(path);
        files.push(path);
      }
      return leading.startsWith(" ") ? " " : "";
    })
    .replace(/\s{2,}/g, " ")
    .trim();

  return { hasRepo, files, cleanText: cleaned };
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

export function repoMentionOptions(): MentionOption[] {
  return [
    {
      kind: "repo",
      id: "repo",
      label: "Current repository",
      detail: "Search the indexed codebase",
      searchText: "repo current repository",
      payload: null,
    },
  ];
}

export function fileMentionOptions(filePaths: string[]): MentionOption[] {
  return filePaths.map((path) => ({
    kind: "file",
    id: path,
    label: path.split("/").pop() ?? path,
    detail: path,
    searchText: path.toLowerCase(),
    payload: path,
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

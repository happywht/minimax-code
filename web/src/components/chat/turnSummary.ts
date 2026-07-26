/**
 * Per-turn summary derivation for assistant messages.
 *
 * Walks the global message log starting just after an assistant
 * message and buckets the tool calls in the same turn into "viewed"
 * (read_file / list_files / …) vs "modified" (write_file / …).
 * The walk stops at the next user or assistant message.
 */
import type { Message } from "../../types/ipc";

export interface TurnSummary {
  thinkingCount: number;
  filesViewed: number;
  filesModified: number;
}

const VIEW_NAMES = new Set([
  "read_file",
  "list_files",
  "glob_files",
  "search_files",
  "list_directory",
]);

const MOD_NAMES = new Set([
  "write_file",
  "edit_file",
  "create_file",
  "delete_file",
  "patch_file",
]);

/**
 * Summarize the turn that starts at `messages[startIdx]`.
 *
 * `thinkingCount` reads `self.metadata?.thinking_count` if the store
 * exposes it; otherwise falls back to 0. The fallback keeps the
 * summary row non-empty even on plain mock-mode runs.
 */
export function summarizeTurn(
  messages: Message[],
  startIdx: number,
  self: Message,
): TurnSummary {
  let filesViewed = 0;
  let filesModified = 0;
  for (let i = startIdx + 1; i < messages.length; i++) {
    const m = messages[i];
    if (m.role === "user" || m.role === "assistant") break;
    if (m.role === "tool" && m.tool_name) {
      if (VIEW_NAMES.has(m.tool_name)) filesViewed += 1;
      else if (MOD_NAMES.has(m.tool_name)) filesModified += 1;
    }
  }
  // ``Message.metadata`` (v0.3.0) carries ``{thinking_count,
  // tokens_in, tokens_out}`` populated by the chat store from the
  // latest ``agent.message_chunk`` event. Falls back to 0 for
  // older runs that haven't migrated yet.
  const thinkingCount = self.metadata?.thinking_count ?? 0;
  return { thinkingCount, filesViewed, filesModified };
}

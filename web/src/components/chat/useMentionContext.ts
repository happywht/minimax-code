/**
 * useMentionContext — turn `@repo` / `#file` markers in the composer
 * into fetched codebase context before the message is sent.
 *
 * This hook is intentionally separate from `useMentionPicker`: the
 * picker only inserts markers, while this hook resolves them into
 * summaries / search results and prepends a context block to the
 * outgoing prompt.
 */
import { useCallback, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores/sessionStore";
import { extractMentionContext } from "../../lib/mentions";
import { toast } from "../layout/ErrorBoundary";
import { strings } from "../../ui/strings";

export interface MentionContextResult {
  /** The text that should actually be sent to the agent. */
  text: string;
  /** True when context was attached. */
  hasContext: boolean;
}

export interface UseMentionContext {
  /** True while fetching summaries / search results. */
  loading: boolean;
  /**
   * Build the final prompt for `value`.
   *
   * Returns `null` when context fetching fails; the caller should
   * abort the send and surface the error.
   */
  buildContext: (value: string) => Promise<MentionContextResult | null>;
}

function formatFileSummary(path: string, summary: string): string {
  return `<file path="${path}">\n${summary.trim()}\n</file>`;
}

function formatSearchResults(query: string, results: { file_path: string; start_line: number; end_line: number; match_line?: number | null; snippet: string }[]): string {
  const lines = results
    .slice(0, 6)
    .map((r) => `- ${r.file_path} ${r.match_line != null ? `match L${r.match_line}; ` : ""}L${r.start_line}-${r.end_line}: ${r.snippet.split("\n")[0] ?? ""}`);
  return `<search query="${query}">\n${lines.join("\n")}\n</search>`;
}

export function useMentionContext(): UseMentionContext {
  const [loading, setLoading] = useState(false);

  const buildContext = useCallback(async (value: string): Promise<MentionContextResult | null> => {
    const { hasRepo, files, cleanText } = extractMentionContext(value);

    if (!hasRepo && files.length === 0) {
      return { text: value, hasContext: false };
    }

    if (hasRepo && !cleanText && files.length === 0) {
      // @repo with no question and no files is ambiguous; avoid an
      // empty codebase search.
      toast.error(strings.chat.toast.mentionTitle, strings.chat.toast.mentionNeedQuestion);
      return null;
    }

    setLoading(true);
    const blocks: string[] = [];
    const state = useSessionStore.getState(), session = state.currentSessionId, project = state.currentProjectId;
    const host = ipc.agentBaseUrl;
    let changed = false;
    const current = () => {
      const latest = useSessionStore.getState();
      return !changed && ipc.agentBaseUrl === host && !!session && !!project && latest.currentSessionId === session &&
        latest.currentProjectId === project && latest.sessions.some((row) => row.id === session && row.project_id === project && !row.archived);
    };
    const off = useSessionStore.subscribe(() => { if (!current()) changed = true; });
    const stop = ipc.onSideCar(() => { changed = true; });

    try {
      if (!current()) throw new Error("请先选择有效的项目与会话，再引用代码库。");
      const identity = await typedIPC.getServiceIdentity();
      if (!identity.service_id || !current()) throw new Error("服务或会话已变化，请重新发送。");
      const scope = { session_id: session!, project_id: project! };
      if (files.length > 0) {
        const summaries = await Promise.all(
          files.map(async (path) => {
            const result = await typedIPC.summarizeCodebasePath(path, scope);
            return formatFileSummary(result.path, result.snippet);
          }),
        );
        blocks.push(...summaries);
      }

      if (hasRepo && cleanText) {
        const search = await typedIPC.searchCodebase(cleanText, { limit: 6, ...scope });
        blocks.push(formatSearchResults(cleanText, search.results));
      }

      const after = await typedIPC.getServiceIdentity();
      if (!current() || after.service_id !== identity.service_id) throw new Error("服务或会话已变化，请重新发送。");
      const context = blocks.join("\n\n");
      const text = context ? `${context}\n\n${cleanText || value}` : cleanText || value;
      return { text, hasContext: true };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.chat.toast.mentionLoadFailed, message);
      return null;
    } finally {
      off(); stop();
      setLoading(false);
    }
  }, []);

  return { loading, buildContext };
}

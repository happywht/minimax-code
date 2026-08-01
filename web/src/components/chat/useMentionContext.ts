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
import { typedIPC } from "../../ipc";
import { extractMentionContext } from "../../lib/mentions";
import { toast } from "../layout/ErrorBoundary";

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

function formatSearchResults(query: string, results: { file_path: string; start_line: number; end_line: number; snippet: string }[]): string {
  const lines = results
    .slice(0, 6)
    .map((r) => `- ${r.file_path} L${r.start_line}-${r.end_line}: ${r.snippet.split("\n")[0] ?? ""}`);
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
      toast.error("Context mention", "Please add a question after @repo.");
      return null;
    }

    setLoading(true);
    const blocks: string[] = [];

    try {
      if (files.length > 0) {
        const summaries = await Promise.all(
          files.map(async (path) => {
            const result = await typedIPC.summarizeCodebasePath(path);
            return formatFileSummary(result.path, result.snippet);
          }),
        );
        blocks.push(...summaries);
      }

      if (hasRepo && cleanText) {
        const search = await typedIPC.searchCodebase(cleanText, { limit: 6 });
        blocks.push(formatSearchResults(cleanText, search.results));
      }

      const context = blocks.join("\n\n");
      const text = context ? `${context}\n\n${cleanText || value}` : cleanText || value;
      return { text, hasContext: true };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to load mention context", message);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  return { loading, buildContext };
}

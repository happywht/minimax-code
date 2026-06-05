/**
 * Code review store — orchestrates the "Run Review" flow.
 *
 * v0.3.1: fetches a git diff via ``gitDiff()``, then invokes the
 * ``code-review:code-review`` skill with the diff as argument. The
 * skill returns structured ``{ comments, stats }`` parsed by this
 * store for the CodeReviewPanel to render.
 */

import { create } from "zustand";
import { typedIPC } from "../ipc";
import { toast } from "../components/ErrorBoundary";

export interface ReviewComment {
  file: string;
  line: number | null;
  severity: "info" | "warning" | "error";
  message: string;
}

export interface ReviewStats {
  files: number;
  additions: number;
  deletions: number;
}

export interface CodeReviewState {
  comments: ReviewComment[];
  stats: ReviewStats | null;
  rawText: string;
  loading: boolean;
  error: string | null;

  runReview: (scope?: "staged" | "branch" | "working") => Promise<void>;
  clear: () => void;
}

export const useCodeReviewStore = create<CodeReviewState>((set) => ({
  comments: [],
  stats: null,
  rawText: "",
  loading: false,
  error: null,

  runReview: async (scope?: "staged" | "branch" | "working") => {
    set({ loading: true, error: null, comments: [], stats: null, rawText: "" });
    try {
      // 1. Get the diff
      const diffResult = await typedIPC.gitDiff({ scope: scope ?? "working" });
      const diff = diffResult.diff;

      if (!diff || diff.trim().length === 0) {
        set({ loading: false, rawText: "No changes to review." });
        toast.info("No changes", "Working tree is clean — nothing to review.");
        return;
      }

      // 2. Invoke code-review skill
      const skillResult = await typedIPC.invokeSkill(
        "code-review:code-review",
        { diff },
      );

      // 3. Parse the result — the skill returns structured or text output
      const output = skillResult.output as Record<string, unknown> | string;
      if (typeof output === "object" && output !== null) {
        const comments = Array.isArray(output.comments)
          ? (output.comments as ReviewComment[])
          : [];
        const stats = output.stats as ReviewStats | null;
        set({ comments, stats, rawText: JSON.stringify(output, null, 2), loading: false });
      } else {
        // Text-only output — display as raw text
        set({ rawText: String(output), loading: false });
      }

      if (typeof output === "object" && output !== null) {
        const comments = (output as Record<string, unknown>).comments;
        const count = Array.isArray(comments) ? comments.length : 0;
        toast.success("Review complete", `${count} comment(s) found`);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ loading: false, error: message });
      toast.error("Review failed", message);
    }
  },

  clear: () => {
    set({ comments: [], stats: null, rawText: "", error: null });
  },

  // expose for tests
  __getState: () => useCodeReviewStore.getState,
}));

/**
 * Code review store — orchestrates the multi-dimensional review flow.
 *
 * v0.3.1: fetches a git diff, invokes the code-review skill.
 * v0.8.0: adds ``runAllChecks()`` that runs security_scan,
 * performance_check, and style checks as separate dimensions,
 * each populating its own tab in CodeReviewPanel.
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

export interface DimensionResult {
  comments: ReviewComment[];
  stats: ReviewStats | null;
  rawText: string;
}

export interface CodeReviewState {
  comments: ReviewComment[];
  stats: ReviewStats | null;
  rawText: string;
  loading: boolean;
  error: string | null;

  /** Per-dimension results keyed by dimension name. */
  dimensions: Record<string, DimensionResult>;

  runReview: (scope?: "staged" | "branch" | "working") => Promise<void>;
  runAllChecks: (path?: string) => Promise<void>;
  clear: () => void;
}

export const useCodeReviewStore = create<CodeReviewState>((set) => ({
  comments: [],
  stats: null,
  rawText: "",
  loading: false,
  error: null,
  dimensions: {},

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

      // 3. Parse the result
      const output = skillResult.output as Record<string, unknown> | string;
      if (typeof output === "object" && output !== null) {
        const comments = Array.isArray(output.comments)
          ? (output.comments as ReviewComment[])
          : [];
        const stats = output.stats as ReviewStats | null;
        set({ comments, stats, rawText: JSON.stringify(output, null, 2), loading: false });
      } else {
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

  runAllChecks: async (path?: string) => {
    set({ loading: true, error: null, dimensions: {} });
    const newDimensions: Record<string, DimensionResult> = {};
    let allComments: ReviewComment[] = [];

    const checks: Array<{
      key: string;
      tool: string;
      args: Record<string, unknown>;
    }> = [
      { key: "security", tool: "security_scan", args: { path: path ?? "." } },
      { key: "performance", tool: "performance_check", args: { path: path ?? "." } },
      { key: "style", tool: "check_style", args: { path: path ?? "." } },
    ];

    try {
      for (const check of checks) {
        try {
          const result = await typedIPC.invokeSkill(
            "code-review:code-review",
            { tool: check.tool, ...check.args },
          );

          const output = result.output as Record<string, unknown> | string;
          if (typeof output === "object" && output !== null) {
            const findings = Array.isArray(output.findings)
              ? (output.findings as Array<Record<string, unknown>>)
              : [];
            const comments: ReviewComment[] = findings.map((f) => ({
              file: String(f.file ?? ""),
              line: f.line != null ? Number(f.line) : null,
              severity: (String(f.severity ?? "info") as ReviewComment["severity"]),
              message: String(f.message ?? ""),
            }));
            newDimensions[check.key] = {
              comments,
              stats: null,
              rawText: JSON.stringify(output, null, 2),
            };
            allComments = allComments.concat(comments);
          } else {
            newDimensions[check.key] = {
              comments: [],
              stats: null,
              rawText: String(output),
            };
          }
        } catch {
          newDimensions[check.key] = {
            comments: [],
            stats: null,
            rawText: `Check '${check.key}' failed.`,
          };
        }
      }

      set({
        loading: false,
        dimensions: newDimensions,
        comments: allComments,
        rawText: "",
        stats: null,
      });

      toast.success(
        "All checks complete",
        `${allComments.length} total finding(s)`,
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ loading: false, error: message, dimensions: newDimensions });
      toast.error("Check failed", message);
    }
  },

  clear: () => {
    set({ comments: [], stats: null, rawText: "", error: null, dimensions: {} });
  },
}));

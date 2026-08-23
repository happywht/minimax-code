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
import { toast } from "../components/layout/ErrorBoundary";
import { strings } from "../ui/strings";

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
        set({ loading: false, rawText: strings.panels.codeReview.noChangesRaw });
        toast.info(
          strings.panels.codeReview.noChanges,
          strings.panels.codeReview.noChangesDetail,
        );
        return;
      }

      // 2. Invoke code-review skill — the typed layer flattens ``{diff}``
      // onto the wire top level so the backend diff route fires.
      const skillResult = await typedIPC.invokeSkill(
        "code-review:code-review",
        { diff },
      );

      // 3. Read the reply per the backend contract (handlers_skills.py):
      // ``text``/``output`` carry the review body, ``comments``/``stats``
      // ride along on the diff route.
      const severityOk = new Set(["info", "warning", "error"]);
      const comments: ReviewComment[] = (skillResult.comments ?? []).map((c) => ({
        file: c.file,
        line: c.line ?? null,
        severity: severityOk.has(c.severity)
          ? (c.severity as ReviewComment["severity"])
          : "info",
        message: c.message,
      }));
      set({
        comments,
        stats: skillResult.stats ?? null,
        rawText: skillResult.output || skillResult.text || "",
        loading: false,
      });

      toast.success(
        strings.panels.codeReview.reviewComplete,
        strings.panels.codeReview.commentCount(comments.length),
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ loading: false, error: message });
      toast.error(strings.panels.codeReview.reviewFailed, message);
    }
  },

  runAllChecks: async (path?: string) => {
    set({ loading: true, error: null, dimensions: {} });
    const newDimensions: Record<string, DimensionResult> = {};
    const allComments: ReviewComment[] = [];

    // Each dimension sends the skill an explicit, self-describing request.
    // The old wire shape ({tool, path}) named tools the backend doesn't
    // have — the LLM received an opaque dict-repr instead of instructions.
    const target = path ?? "the working tree";
    const checks: Array<{ key: string; request: string }> = [
      {
        key: "security",
        request:
          `Security review of ${target}: look for injection risks, unsafe ` +
          "deserialization, hardcoded secrets, and permission gaps. Report each " +
          "finding as file, line, severity (info|warning|error), and a one-line message.",
      },
      {
        key: "performance",
        request:
          `Performance review of ${target}: look for quadratic hot paths, ` +
          "redundant I/O in loops, unbounded memory growth, and needless " +
          "re-computation. Report each finding as file, line, severity " +
          "(info|warning|error), and a one-line message.",
      },
      {
        key: "style",
        request:
          `Style review of ${target}: look for naming inconsistencies, dead ` +
          "code, oversized functions, and comment/code drift. Report each " +
          "finding as file, line, severity (info|warning|error), and a one-line message.",
      },
    ];

    try {
      for (const check of checks) {
        try {
          const result = await typedIPC.invokeSkill(
            "code-review:code-review",
            { request: check.request },
          );

          // The skill returns free-form review text — surface it verbatim
          // in the dimension tab (findings stay unstructured until the
          // backend emits them as data).
          const body = result.output || result.text || "";
          newDimensions[check.key] = {
            comments: [],
            stats: null,
            rawText: body || strings.panels.codeReview.checkFailedRaw(check.key),
          };
        } catch {
          newDimensions[check.key] = {
            comments: [],
            stats: null,
            rawText: strings.panels.codeReview.checkFailedRaw(check.key),
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
        strings.panels.codeReview.allChecksComplete,
        strings.panels.codeReview.findingCount(allComments.length),
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ loading: false, error: message, dimensions: newDimensions });
      toast.error(strings.panels.codeReview.checkFailed, message);
    }
  },

  clear: () => {
    set({ comments: [], stats: null, rawText: "", error: null, dimensions: {} });
  },
}));

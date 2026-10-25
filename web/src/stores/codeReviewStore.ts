/** Durable Native review runs; browser state is a receipt index. */
import { create } from "zustand";
import { typedIPC } from "../ipc";
import type { AgentRunStatus, SkillInvokeArgs, SkillInvokeResult } from "../types/ipc";
import { useSessionStore } from "./sessionStore";
import { useRunTimelineStore } from "./runStore";

export interface ReviewComment { file: string; line: number | null; severity: "info" | "warning" | "error"; message: string }
export interface ReviewStats { files: number; additions: number; deletions: number }
export type ReviewDimension = "overview" | "security" | "performance" | "style";
export interface DimensionResult {
  comments: ReviewComment[]; stats: ReviewStats | null; rawText: string;
  runId?: string; status?: AgentRunStatus | "admission_unknown"; error?: string; partial?: boolean;
}
interface Invocation { args: SkillInvokeArgs; runId?: string; status: AgentRunStatus | "admission_unknown" }
export interface CodeReviewState {
  sessionId: string | null; comments: ReviewComment[]; stats: ReviewStats | null; rawText: string;
  loading: boolean; error: string | null; dimensions: Record<string, DimensionResult>;
  invocations: Partial<Record<ReviewDimension, Invocation>>;
  bindSession: (sessionId: string | null) => Promise<void>;
  refresh: () => Promise<void>;
  runReview: (scope?: "staged" | "branch" | "working") => Promise<void>;
  runAllChecks: (path?: string) => Promise<void>;
  retryPending: (dimension: ReviewDimension) => Promise<void>;
  cancel: (dimension: ReviewDimension) => Promise<void>;
  openRun: (dimension: ReviewDimension) => Promise<boolean>;
  clear: () => void;
}
const active = new Set(["queued", "planning", "running", "awaiting_approval", "waiting_input"]);
let generation = 0;
let revision = 0;
let starting: symbol | null = null;
const storageKey = (sid: string) => `minimax-code:reviews:${sid}`;
function save(sid: string, invocations: CodeReviewState["invocations"]): void {
  // Persist the key before sending. Storage failure must stop admission.
  localStorage.setItem(storageKey(sid), JSON.stringify(invocations));
}
function read(sid: string): CodeReviewState["invocations"] {
  try { return JSON.parse(localStorage.getItem(storageKey(sid)) ?? "{}"); } catch { return {}; }
}
function nativeSession(): string {
  const state = useSessionStore.getState();
  const session = state.sessions.find((item) => item.id === state.currentSessionId);
  if (!session || session.engine === "codex") throw new Error("请选择一个 Native 会话及其工作区");
  return session.id;
}
function resultView(result: SkillInvokeResult, status: AgentRunStatus): DimensionResult {
  return { comments: (result.comments ?? []).map((comment) => ({ ...comment,
    severity: ["info", "warning", "error"].includes(comment.severity) ? comment.severity as ReviewComment["severity"] : "info" })),
    stats: result.stats ?? null, rawText: result.output ?? result.text ?? "", runId: result.run_id,
    status, error: result.error?.message,
    partial: result.partial || result.truncated || result.cancelled || result.budget_exhausted };
}
export const useCodeReviewStore = create<CodeReviewState>((set, get) => {
  const current = (sid: string, epoch: number) => generation === epoch && get().sessionId === sid &&
    useSessionStore.getState().currentSessionId === sid;
  const update = (sid: string, dimension: ReviewDimension, invocation: Invocation) => {
    const invocations = { ...get().invocations, [dimension]: invocation };
    save(sid, invocations);
    revision++;
    set({ invocations });
  };
  const admit = async (dimension: ReviewDimension, invocation: Invocation) => {
    const sid = invocation.args.session_id, epoch = generation;
    if (!current(sid, epoch)) return;
    try {
      update(sid, dimension, invocation);
      set({ error: null });
      const admitted = await typedIPC.admitSkill("code-review:code-review", invocation.args);
      if (!current(sid, epoch) || get().invocations[dimension]?.args.idempotency_key !== invocation.args.idempotency_key) return;
      update(sid, dimension, { ...invocation, runId: admitted.run.id, status: admitted.run.status });
      await get().refresh();
    } catch (err) {
      if (!current(sid, epoch) || get().invocations[dimension]?.args.idempotency_key !== invocation.args.idempotency_key) return;
      // Another retry may already have confirmed this same stable key.
      // Its retained run cannot be downgraded by an older lost-ACK response.
      if (get().invocations[dimension]?.runId) return;
      const message = err instanceof Error ? err.message : String(err);
      const refused = typeof err === "object" && err !== null && "code" in err && err.code === -32602;
      if (refused) update(sid, dimension, { ...invocation, status: "failed" });
      set({ error: refused ? message : `接单结果未确认，请使用原请求重试：${message}`,
        loading: Boolean(starting) || Object.values(get().invocations).some((item) => item && active.has(item.status)),
        dimensions: { ...get().dimensions, [dimension]: { comments: [], stats: null, rawText: "",
          status: refused ? "failed" : "admission_unknown", error: message } } });
    }
  };
  const start = async (scope: "staged" | "branch" | "working", all: boolean, path?: string) => {
    let epoch = generation;
    const token = Symbol("review-admission");
    try {
      const sid = nativeSession();
      if (get().sessionId !== sid) { await get().bindSession(sid); epoch = generation; }
      if (starting || get().loading || Object.values(get().invocations).some((item) => item &&
          (item.status === "admission_unknown" || active.has(item.status))))
        throw new Error("请先确认已有请求，勿重复接单");
      starting = token;
      revision++;
      set({ loading: true, error: null });
      const diff = await typedIPC.gitDiff({ session_id: sid, scope });
      if (!current(sid, epoch)) return;
      if (!diff.workspace || diff.session_id !== sid) throw new Error("Git 未返回绑定工作区，无法安全审查");
      if (!all && !diff.diff.trim()) { set({ loading: false, rawText: "当前工作区没有已跟踪文件差异" }); return; }
      const labels: ReviewDimension[] = all ? ["security", "performance", "style"] : ["overview"];
      const invocations = { ...get().invocations };
      for (const label of labels) {
        const request = label === "overview" ? "Review this unified diff" :
          `${label} review of ${path ?? "the selected workspace"}. Inspect relevant files and report findings with file/line/severity. Describe tool failures and uncertainty. ` +
          (label === "security" ? "Look for injection, secrets and permission gaps." :
            label === "performance" ? "Look for costly loops, I/O and unbounded memory." : "Look for naming, dead code and complexity.");
        invocations[label] = { status: "admission_unknown", args: { request, session_id: sid,
          idempotency_key: `review:${crypto.randomUUID()}`, expected_workspace: diff.workspace, label,
          ...(label === "overview" ? { diff: diff.diff } : {}) } };
      }
      // All three checks remain distinct admitted roots and budget families.
      save(sid, invocations);
      revision++;
      set({ invocations });
      for (const label of labels) { if (!current(sid, epoch)) return; await admit(label, invocations[label]!); }
      if (current(sid, epoch)) set({ loading: Object.values(get().invocations).some((item) => active.has(item!.status)) });
    } catch (err) {
      if (generation === epoch) set({ loading: Boolean(starting) || Object.values(get().invocations).some((item) => item && active.has(item.status)),
        error: err instanceof Error ? err.message : String(err) });
    } finally {
      if (starting === token) {
        starting = null;
        if (generation === epoch) set({loading:Object.values(get().invocations).some((item) => item && active.has(item.status))});
      }
    }
  };
  return {
    sessionId: null, comments: [], stats: null, rawText: "", loading: false, error: null, dimensions: {}, invocations: {},
    bindSession: async (sid) => {
      generation++;
      revision++;
      starting = null;
      set({ sessionId: sid, comments: [], stats: null, rawText: "", loading: false, error: null,
        dimensions: {}, invocations: sid ? read(sid) : {} });
      if (sid) await get().refresh();
    },
    refresh: async () => {
      const sid = get().sessionId, epoch = generation;
      const version = revision;
      const valid = () => sid !== null && current(sid, epoch) && revision === version;
      if (!sid || !current(sid, epoch)) return;
      try {
        const { runs } = await typedIPC.listRuns({ session_id: sid, limit: 100 });
        if (!valid()) return;
        const invocations = structuredClone(get().invocations);
        for (const run of runs) {
          const profile = run.metadata?.skill_execution as { skill_id?: string; label?: ReviewDimension } | undefined;
          if (profile?.skill_id !== "code-review:code-review" || !profile.label) continue;
          const existing = invocations[profile.label];
          if (!existing) invocations[profile.label] = { args: { session_id: sid, idempotency_key: run.request_key ?? "",
            request: "Restored review", label: profile.label }, runId: run.id, status: run.status };
          else if (!existing.runId && existing.args.idempotency_key === run.request_key) existing.runId = run.id;
        }
        const dimensions = { ...get().dimensions };
        for (const [label, invocation] of Object.entries(invocations)) {
          if (!invocation?.runId) continue;
          const snapshot = await typedIPC.getRun(invocation.runId);
          if (!valid()) return;
          if (snapshot.run.session_id !== sid || snapshot.run.id !== invocation.runId) throw new Error("审查记录与会话不一致");
          invocation.status = snapshot.run.status;
          const result = snapshot.result;
          dimensions[label] = result && "skill_id" in result ? resultView(result as unknown as SkillInvokeResult, snapshot.run.status) :
            { comments: [], stats: null, rawText: "", runId: invocation.runId, status: snapshot.run.status,
              error: result && "error" in result ? result.error?.message : snapshot.run.error ?? undefined };
        }
        if (!valid()) return;
        save(sid, invocations);
        revision++;
        const overview = dimensions.overview;
        set({ invocations, dimensions, loading: Boolean(starting) || Object.values(invocations).some((item) => active.has(item!.status)),
          ...(overview ? { comments: overview.comments, stats: overview.stats, rawText: overview.rawText } : {}) });
      } catch (err) {
        if (valid()) set({ error: err instanceof Error ? err.message : String(err),
          loading: Boolean(starting) || Object.values(get().invocations).some((item) => item && active.has(item.status)) });
      }
    },
    runReview: (scope = "working") => start(scope, false),
    runAllChecks: (path) => start("working", true, path),
    retryPending: async (dimension) => { const invocation = get().invocations[dimension]; if (invocation?.status === "admission_unknown") await admit(dimension, invocation); },
    cancel: async (dimension) => {
      const sid = get().sessionId, epoch = generation, runId = get().invocations[dimension]?.runId;
      if (!sid || !runId || !current(sid, epoch)) return;
      await typedIPC.cancelRun(runId);
      if (current(sid, epoch)) await get().refresh();
    },
    openRun: async (dimension) => {
      const sid = get().sessionId, epoch = generation, runId = get().invocations[dimension]?.runId;
      if (!sid || !runId || !current(sid, epoch)) return false;
      const snapshot = await typedIPC.getRunSteps(runId);
      if (!current(sid, epoch) || get().invocations[dimension]?.runId !== runId) return false;
      if (snapshot.run.id !== runId || snapshot.run.session_id !== sid) throw new Error("任务记录与审查会话不一致");
      useRunTimelineStore.setState((state) => ({ runs: { ...state.runs, [runId]: { ...snapshot.run, steps: snapshot.steps } },
        order: [runId, ...state.order.filter((id) => id !== runId)] }));
      return true;
    },
    clear: () => { set({ comments: [], stats: null, rawText: "", error: null, dimensions: {} }); },
  };
});

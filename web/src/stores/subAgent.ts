/** Durable public roots and progress-only internally spawned children. */
import { create } from "zustand";
import { ipc, typedIPC } from "../ipc";
import { StreamEvent, type SubAgentProgress, type SubAgentRun, type SubAgentStatus,
  type SpawnSubagentParams, type AgentRunStatus, type SendMessageResult, type SpawnSubagentResult } from "../types/ipc";
import { evictOldest, MAX_SUBAGENT_RUNS } from "../lib/eviction";
import { useSessionStore } from "./sessionStore";
import { useRunTimelineStore } from "./runStore";

export interface SubAgentState {
  runs: Record<string, SubAgentRun>; subscribed: boolean; error: string | null;
  init: () => Promise<void>;
  register: (run: SubAgentRun) => void;
  applyProgress: (progress: SubAgentProgress) => void;
  spawn: (args: SpawnSubagentParams) => Promise<void>;
  retry: (runId: string) => Promise<void>;
  refresh: () => Promise<void>;
  recoverChildren: (sessionId: string) => Promise<void>;
  openRun: (runId: string) => Promise<boolean>;
  cancelRun: (runId: string) => Promise<void>;
  clearCompleted: () => void;
  reset: () => void;
}
let unsubProgress: (() => void) | null = null;
let unsubRuns: Array<() => void> = [];
let childEpoch = 0;
let childScope = "";
let missingChildWake = false;
type ChildList = { session_id: string; project_id: string | null; service_id: string; limit: number; children: Array<{
  run_id: string; session_id: string; parent_run_id: string; status: AgentRunStatus; live: boolean;
  agent_id: string; agent_name: string; prompt: string; text: string; partial: boolean; error: string | null;
  text_truncated: boolean;
  created_at: string; completed_at: string | null;
}> };
const storageKey = "minimax-code:agent-roots:v1";
const terminal = new Set<AgentRunStatus>(["completed", "failed", "cancelled", "interrupted", "budget_paused"]);
const finished = (status: SubAgentStatus) => ["completed", "failed", "cancelled"].includes(status);
function persist(runs: Record<string, SubAgentRun>): void {
  localStorage.setItem(storageKey, JSON.stringify(Object.fromEntries(Object.entries(runs).filter(([, row]) => row.request))));
}
function restore(): Record<string, SubAgentRun> {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) ?? "{}");
    return Object.fromEntries(Object.entries(saved).filter(([, value]) => {
      const row = value as SubAgentRun;
      return row?.request?.session_id && row.request.idempotency_key && typeof row.run_id === "string";
    })) as Record<string, SubAgentRun>;
  } catch { return {}; }
}
function statusView(status: AgentRunStatus, partial = false): SubAgentStatus {
  if (status === "completed") return partial ? "failed" : "completed";
  if (status === "cancelled") return "cancelled";
  return terminal.has(status) ? "failed" : "thinking";
}
export const useSubAgentStore = create<SubAgentState>((set, get) => ({
  runs: {}, subscribed: false, error: null,
  init: async () => {
    if (get().subscribed) { await get().refresh(); return; }
    set({ runs: { ...restore(), ...get().runs } });
    await ipc.start();
    if (!unsubProgress) unsubProgress = ipc.on<SubAgentProgress>(StreamEvent.SubAgentProgress, (env) => {
      if (env.data?.run_id) {
        const missing = !get().runs[env.data.run_id];
        get().applyProgress({ ...env.data, received_at: env.data.received_at ?? Date.now() });
        const sameScope = childScope && env.data.parent_session_id === useSessionStore.getState().currentSessionId;
        if (sameScope && (finished(env.data.status) || (env.data.status as string) === "interrupted")) {
          // Terminal progress is emitted after the durable status update.
          void get().recoverChildren(env.data.parent_session_id!);
        } else if (missing && !missingChildWake && sameScope) {
          missingChildWake = true;
          void get().recoverChildren(env.data.parent_session_id!).finally(() => { missingChildWake = false; });
        }
      }
    });
    if (!unsubRuns.length) {
      const recover = () => { const id = useSessionStore.getState().currentSessionId; if (id) void get().recoverChildren(id); };
      unsubRuns = [StreamEvent.RunUpdated, StreamEvent.RunCompleted].map((event) =>
        ipc.on(event, () => { void get().refresh(); recover(); }));
      unsubRuns.push(ipc.onSideCar((event) => {
        if (event.status === "started") { childScope = ""; recover(); }
        if (event.status === "stopped" || event.status === "error") {
          childEpoch++;
          set((s) => ({ runs: Object.fromEntries(Object.entries(s.runs).map(([id, row]) =>
            [id, row.recovery_service_id && row.run_status && !terminal.has(row.run_status)
              ? { ...row, status: "unknown" as const, summary: "连接中断，运行状态待核对" } : row])) }));
        }
      }));
    }
    set({ subscribed: true });
    await get().refresh();
  },
  register: (run) => set((s) => ({ runs: evictOldest({ ...s.runs, [run.run_id]: run }, MAX_SUBAGENT_RUNS, (r) => r.updated_at) })),
  applyProgress: (progress) => set((s) => {
    const old = s.runs[progress.run_id];
    if (old?.request) return s;
    if (!old && childScope) return s;
    // Durable status and ownership take precedence over replayed progress events.
    if (old?.recovery_service_id) {
      if (old.status !== "thinking" || progress.parent_session_id !== old.parent_session_id) return s;
      return { runs: { ...s.runs, [old.run_id]: { ...old, progress: progress.progress ?? old.progress,
        summary: progress.summary ?? old.summary, text: progress.text ?? old.text, updated_at: progress.received_at } } };
    }
    const allowed: SubAgentStatus[] = ["started", "thinking", "tool_call", "tool_result", "completed", "failed", "cancelled"];
    const status = allowed.includes(progress.status) ? progress.status : "started";
    const row: SubAgentRun = {
      ...old, run_id: progress.run_id, agent_id: progress.agent_id, agent_name: old?.agent_name ?? progress.agent_id,
      prompt: old?.prompt ?? "", started_at: old?.started_at ?? progress.received_at,
      parent_session_id: progress.parent_session_id ?? old?.parent_session_id,
      context_message_id: progress.context_message_id ?? old?.context_message_id,
      status, progress: progress.progress ?? old?.progress ?? 0, summary: progress.summary ?? old?.summary ?? "",
      updated_at: progress.received_at, text: progress.text ?? old?.text, error: progress.error ?? old?.error,
      finished_at: finished(status) ? (old?.finished_at ?? progress.received_at) : old?.finished_at,
    };
    return { runs: evictOldest({ ...s.runs, [row.run_id]: row }, MAX_SUBAGENT_RUNS, (r) => r.updated_at) };
  }),
  spawn: async (args) => {
    const session = useSessionStore.getState().sessions.find((item) => item.id === args.session_id);
    if (!session || session.engine === "codex") throw new Error("请选择既有 Native 来源会话");
    if (!args.idempotency_key.trim() || !args.prompt.trim()) throw new Error("请求及稳定接单键不能为空");
    const find = () => Object.values(get().runs).find((row) => row.request?.session_id === args.session_id && row.request.idempotency_key === args.idempotency_key);
    const old = find();
    if (old && JSON.stringify(old.request) !== JSON.stringify(args)) throw new Error("请求键已属于其他输入");
    const pendingId = old?.run_id ?? `pending_${args.idempotency_key}`;
    const row: SubAgentRun = old ?? {
      run_id: pendingId, agent_id: args.agent_id, agent_name: args.agent_name ?? args.agent_id, prompt: args.prompt,
      parent_session_id: args.session_id, display_name: args.display_name, status: "started", progress: 0,
      summary: "接单结果待确认", started_at: Date.now(), updated_at: Date.now(), request: args, admission_unknown: true,
    };
    const runs = { ...get().runs, [pendingId]: row };
    persist(runs);
    set({ runs, error: null });
    try {
      const admitted = await typedIPC.admitSubagent(args);
      const current = find();
      if (!current) return;
      if (current.session_id) {
        if (current.run_id !== admitted.run.id || current.session_id !== admitted.run.session_id) {
          throw new Error("原请求返回了不同运行，请核对持久接单记录");
        }
        await get().refresh();
        return;
      }
      const next = { ...get().runs };
      delete next[current.run_id];
      next[admitted.run.id] = { ...current, run_id: admitted.run.id, session_id: admitted.run.session_id,
        run_status: admitted.run.status, admission_unknown: false, status: statusView(admitted.run.status),
        summary: admitted.run.status, updated_at: Date.now() };
      persist(next); set({ runs: next });
      await get().refresh();
    } catch (err) {
      const current = find();
      if (!current || current.session_id) return;
      const message = err instanceof Error ? err.message : String(err);
      const refused = typeof err === "object" && err !== null && "code" in err && err.code === -32602;
      const next = { ...get().runs, [current.run_id]: { ...current, status: refused ? "failed" as const : current.status,
        admission_unknown: !refused, error: message, summary: refused ? message : "接单未确认，请使用原请求重试" } };
      persist(next); set({ runs: next, error: message });
    }
  },
  retry: async (id) => { const row = get().runs[id]; if (row?.request) await get().spawn(row.request); },
  recoverChildren: async (sessionId) => {
    const state = useSessionStore.getState();
    const session = state.sessions.find((row) => row.id === sessionId);
    if (!session || state.currentSessionId !== sessionId) return;
    const project = session.project_id ?? null, host = ipc.agentBaseUrl;
    const scope = JSON.stringify([host, sessionId, project]);
    if (scope !== childScope) {
      childScope = scope;
      set((s) => ({ runs: Object.fromEntries(Object.entries(s.runs).filter(([, row]) => !!row.request)) }));
    }
    const ticket = ++childEpoch;
    const current = () => ticket === childEpoch && childScope === scope && ipc.agentBaseUrl === host
      && useSessionStore.getState().currentSessionId === sessionId
      && (useSessionStore.getState().sessions.find((row) => row.id === sessionId)?.project_id ?? null) === project;
    try {
      const data = await ipc.request<ChildList>("run.child_list", { session_id: sessionId });
      if (!current()) return;
      if (data.session_id !== sessionId || data.project_id !== project || !data.service_id) throw new Error("子任务恢复绑定不一致");
      set((s) => {
        const next = Object.fromEntries(Object.entries(s.runs).filter(([, row]) => !!row.request));
        for (const child of data.children) {
          if (!child.run_id || !child.parent_run_id || next[child.run_id]?.request) continue;
          const old = s.runs[child.run_id];
          const active = !terminal.has(child.status);
          const status = active && !child.live ? "unknown" : statusView(child.status, child.partial);
          const started = Date.parse(child.created_at) || Date.now();
          next[child.run_id] = { run_id: child.run_id, session_id: child.session_id,
            parent_session_id: sessionId, parent_run_id: child.parent_run_id, recovery_service_id: data.service_id,
            agent_id: child.agent_id, agent_name: child.agent_name || child.agent_id || "子 Agent", prompt: child.prompt,
            run_status: child.status, status, partial: child.partial, error: child.error || undefined,
            progress: active ? old?.progress ?? 0 : 1, summary: active && !child.live ? "持久状态未终结，运行状态待核对" : child.status,
            text: child.text || undefined, text_truncated: child.text_truncated, started_at: started, updated_at: Date.now(),
            finished_at: terminal.has(child.status) ? (Date.parse(child.completed_at || "") || Date.now()) : undefined };
        }
        return { runs: next };
      });
    } catch (err) {
      if (current()) set({ error: err instanceof Error ? err.message : String(err) });
    }
  },
  refresh: async () => {
    for (const [id, row] of Object.entries(get().runs)) {
      if (!row.request || !row.session_id) continue;
      try {
        const snapshot = await typedIPC.getRun(id);
        if (get().runs[id] !== row) continue;
        if (snapshot.run.id !== id || snapshot.run.session_id !== row.session_id) throw new Error("运行回执身份不一致");
        const receipt = snapshot.result as (SendMessageResult & Partial<SpawnSubagentResult>) | null;
        const partial = Boolean(receipt?.partial || receipt?.verification?.requires_review);
        const next = { ...get().runs, [id]: { ...row, run_status: snapshot.run.status, status: statusView(snapshot.run.status, partial),
          text: receipt?.text ?? row.text, partial, error: snapshot.run.error ?? undefined,
          execution_target: receipt?.execution_target ?? row.execution_target,
          summary: partial ? `${snapshot.run.status} · 部分结果，需核对` : snapshot.run.status,
          updated_at: Date.now(), finished_at: terminal.has(snapshot.run.status) ? (row.finished_at ?? Date.now()) : undefined } };
        persist(next); set({ runs: next });
      } catch (err) {
        if (get().runs[id] === row) set({ error: err instanceof Error ? err.message : String(err) });
      }
    }
  },
  openRun: async (id) => {
    const row = get().runs[id], source = useSessionStore.getState().currentSessionId;
    if (!row?.session_id || source !== row.parent_session_id) return false;
    const snapshot = await typedIPC.getRunSteps(id);
    if (get().runs[id] !== row || useSessionStore.getState().currentSessionId !== source) return false;
    if (snapshot.run.id !== id || snapshot.run.session_id !== row.session_id) throw new Error("运行回执身份不一致");
    await useSessionStore.getState().refresh({ loadCurrent: false });
    if (get().runs[id] !== row || useSessionStore.getState().currentSessionId !== source) return false;
    if (!useSessionStore.getState().sessions.some((session) => session.id === row.session_id)) return false;
    if (!useSessionStore.getState().setCurrent(row.session_id)) return false;
    useRunTimelineStore.setState((s) => ({ runs: { ...s.runs, [id]: { ...snapshot.run, steps: snapshot.steps } },
      order: [id, ...s.order.filter((item) => item !== id)] }));
    return useSessionStore.getState().currentSessionId === row.session_id;
  },
  cancelRun: async (id) => {
    const row = get().runs[id];
    if (!row) return;
    try {
      const result = await typedIPC.cancelSubagent(id);
      if (get().runs[id] !== row) return;
      const next = { ...get().runs, [id]: { ...row, cancel_requested: result.cancelled,
        summary: result.cancelled ? "取消已请求，等待运行与清理终态" : row.summary, updated_at: Date.now() } };
      persist(next); set({ runs: next }); await get().refresh();
    } catch (err) {
      if (get().runs[id] === row) set({ error: err instanceof Error ? err.message : String(err) });
    }
  },
  clearCompleted: () => set((s) => {
    const runs = Object.fromEntries(Object.entries(s.runs).filter(([, row]) => !finished(row.status) ||
      row.admission_unknown || row.run_status === "interrupted" || row.run_status === "budget_paused"));
    persist(runs); return { runs };
  }),
  reset: () => { childEpoch++; childScope = ""; set({ runs: {}, subscribed: false, error: null }); },
}));
export function runsForSession(state: SubAgentState, sessionId: string | null | undefined): SubAgentRun[] {
  return Object.values(state.runs).filter((row) => !sessionId || row.parent_session_id === sessionId);
}

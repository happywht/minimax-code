/**
 * Team run store — tracks active team spawn runs and progress events.
 *
 * Subscribes to ``agent.team_progress`` WebSocket events and maintains
 * a per-task-id progress panel that the UI renders.
 *
 * v0.8.0 — Enterprise Multi-Agent.
 */

import { create } from "zustand";
import { typedIPC, ipc } from "../ipc/client";
import { StreamEvent } from "../types/ipc";
import type { TeamProgressData } from "../types/ipc";
import { toast } from "../components/layout/ErrorBoundary";
import { trimArray, MAX_TEAM_RUNS } from "../lib/eviction";
import { strings } from "../ui/strings";
import { useSessionStore } from "./sessionStore";

/** A single team run tracked in the UI. */
export interface TeamRunEntry {
  task_id: string;
  team_name: string;
  session_id?: string;
  cancelling?: boolean;
  idempotency_key?: string;
  max_parallel?: number;
  status: TeamProgressData["status"];
  progress: number;
  agents_total: number;
  agents_completed: number;
  agent_name?: string;
  summary?: string;
  started_at: number;
  updated_at: number;
  /** Final result — set once status is "completed" or "failed". */
  result?: {
    merged_text: string;
    success: boolean;
    conflicts: Array<{ file_path: string; agents: string[]; conflict_type: string }>;
    budget?: { known_tokens: number; reserved_tokens: number; unknown_attempts: number; usage_complete: boolean; limit_tokens: number | null } | null;
    agents_run: Array<{ agent_name: string; workspace?: { run_id: string } }>;
  };
}

export interface TeamRunState {
  runs: TeamRunEntry[];

  /** Spawn a team and track its progress. */
  spawn: (opts: {
    team_name: string;
    request: string;
    session_id?: string;
    parent_session_id?: string;
    budget_tokens?: number;
    mode?: "execute";
    idempotency_key?: string;
    max_parallel?: number;
  }) => Promise<void>;

  /** Clear completed/failed runs older than `maxAgeMs`. */
  prune: (maxAgeMs?: number) => void;

  /** Remove a specific run by task_id. */
  remove: (task_id: string) => void;
  cancel: (task_id: string) => Promise<void>;
}

/** Unsubscribe handle for the WS listener. */
let _unsubscribe: (() => void) | null = null;

export const useTeamRunStore = create<TeamRunState>((set, _get) => ({
  runs: [],

  spawn: async (opts) => {
    // Insert an optimistic entry immediately so the panel shows the run
    // even before the first WS progress event arrives (or if WS is down).
    const pendingId = `pending-${opts.team_name}-${Date.now()}`;
    const requestKey = opts.idempotency_key ?? crypto.randomUUID();
    set((s) => ({
      runs: trimArray(
        [
          ...s.runs,
          {
            task_id: pendingId,
            team_name: opts.team_name,
            session_id: opts.session_id ?? opts.parent_session_id,
            idempotency_key: requestKey,
            max_parallel: opts.max_parallel,
            status: "queued",
            progress: 0,
            agents_total: 0,
            agents_completed: 0,
            started_at: Date.now(),
            updated_at: Date.now(),
          },
        ],
        MAX_TEAM_RUNS,
      ),
    }));
    try {
      const result = await typedIPC.spawnTeam({ ...opts, idempotency_key: requestKey });
      set((s) => ({
        runs: s.runs.map((r) =>
          // The pending entry we just inserted, or the real-task_id entry
          // the WS listener created from the first progress event.
          r.task_id === pendingId || (result.task_id && r.task_id === result.task_id)
            ? {
                ...r,
                task_id: result.task_id ?? r.task_id,
                status: result.success ? "completed" : "failed",
                progress: 1,
                max_parallel: result.max_parallel ?? r.max_parallel,
                updated_at: Date.now(),
                result: {
                  merged_text: result.merged_text,
                  success: result.success,
                  conflicts: result.conflicts,
                  budget: result.budget,
                  agents_run: result.agents_run,
                },
              }
            : r,
        ),
      }));
    } catch (e) {
      // Mark the pending run as failed
      const msg = String(e);
      if (!_get().runs.some((r) => r.idempotency_key === requestKey && (r.status === "interrupted" || r.status === "cancelled"))) {
        toast.error(strings.toasts.teamSpawnFailed, msg);
      }
      set((s) => ({
        runs: s.runs.map((r) =>
          r.task_id === pendingId ? { ...r, status: "failed", updated_at: Date.now() } : r,
        ),
      }));
    }
  },

  prune: (maxAgeMs = 60_000) => {
    const cutoff = Date.now() - maxAgeMs;
    set((s) => ({
      runs: s.runs.filter(
        (r) =>
          (r.status !== "completed" && r.status !== "failed" && r.status !== "cancelled" && r.status !== "interrupted") ||
          r.updated_at > cutoff,
      ),
    }));
  },

  remove: (task_id) => {
    set((s) => ({ runs: s.runs.filter((r) => r.task_id !== task_id) }));
  },
  cancel: async (task_id) => {
    const run = _get().runs.find((r) => r.task_id === task_id);
    if (!run || run.session_id !== useSessionStore.getState().currentSessionId || run.cancelling) return;
    set((s) => ({ runs: s.runs.map((r) => r.task_id === task_id ? { ...r, cancelling: true } : r) }));
    try {
      const result = await typedIPC.cancelRun(task_id);
      if (!result.requested) throw new Error("运行已结束或服务重启，无法发送停止请求；请刷新运行记录。");
    } catch (error) {
      toast.error("无法停止团队", String(error));
      set((s) => ({ runs: s.runs.map((r) => r.task_id === task_id ? { ...r, cancelling: false } : r) }));
    }
  },
}));

// ── WebSocket subscription ──────────────────────────────────────────────────

/**
 * Subscribe to ``agent.team_progress`` events once.  Call this from
 * a top-level mount (App.tsx or RightPanel).
 */
export function initTeamRunListener(): () => void {
  if (_unsubscribe) return _unsubscribe;

  const unsub = ipc.on<TeamProgressData>(StreamEvent.TeamProgress, (env) => {
    // The callback receives the event *envelope*; the progress payload
    // itself lives in `env.data`. Casting the envelope to the payload
    // type made every field access return undefined, so each progress
    // event appended a ghost entry and the panel never updated.
    const evt = env.data;
    if (!evt) return;
    useTeamRunStore.setState((s) => {
      const existing = s.runs.find((r) => r.task_id === evt.task_id);
      if (existing) {
        return {
          runs: s.runs.map((r) =>
            r.task_id === evt.task_id
              ? {
                  ...r,
                  status: r.status === "interrupted" || (evt.status === "queued" && r.status !== "queued") ? r.status : evt.status,
                  progress: evt.progress,
                  agents_total: evt.agents_total ?? r.agents_total,
                  agents_completed: evt.agents_completed ?? r.agents_completed,
                  agent_name: evt.agent_name ?? r.agent_name,
                  summary: evt.summary ?? r.summary,
                  max_parallel: evt.max_parallel ?? r.max_parallel,
                  updated_at: Date.now(),
                }
              : r,
          ),
        };
      }
      // New run — replace a same-team optimistic "pending-*" entry instead
      // of appending a duplicate card.
      const pendingIdx = s.runs.findIndex(
        (r) => r.team_name === evt.team_name && r.session_id === evt.session_id && r.task_id.startsWith("pending-") &&
          (!evt.idempotency_key || r.idempotency_key === evt.idempotency_key),
      );
      const entry: TeamRunEntry = {
        task_id: evt.task_id,
        team_name: evt.team_name,
        session_id: evt.session_id,
        idempotency_key: evt.idempotency_key,
        max_parallel: evt.max_parallel,
        status: evt.status,
        progress: evt.progress,
        agents_total: evt.agents_total ?? 0,
        agents_completed: evt.agents_completed ?? 0,
        agent_name: evt.agent_name,
        summary: evt.summary,
        started_at: Date.now(),
        updated_at: Date.now(),
      };
      if (pendingIdx !== -1) {
        const next = [...s.runs];
        next[pendingIdx] = entry;
        return { runs: next };
      }
      return { runs: trimArray([...s.runs, entry], MAX_TEAM_RUNS) };
    });
  });

  _unsubscribe = () => {
    unsub();
    _unsubscribe = null;
  };
  return _unsubscribe;
}

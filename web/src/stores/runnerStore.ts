import { create } from "zustand";
import { typedIPC } from "../ipc";
import type {
  RunnerApprovalPolicy,
  RunnerInfo,
  RunnerPermissionMode,
  RunnerSandboxMode,
  RunnerStartResult,
} from "../types/ipc";

export interface RunnerState {
  runners: RunnerInfo[];
  selectedId: RunnerInfo["id"];
  loading: boolean;
  starting: boolean;
  error: string | null;
  lastStart: RunnerStartResult | null;
  setSelected: (runnerId: RunnerInfo["id"]) => void;
  list: () => Promise<void>;
  start: (opts: {
    command: string;
    cwd?: string;
    session_id?: string | null;
    timeout_s?: number;
    sandbox_mode?: RunnerSandboxMode;
    approval_policy?: RunnerApprovalPolicy;
    permission_mode?: RunnerPermissionMode;
  }) => Promise<RunnerStartResult | null>;
  reset: () => void;
}

export const useRunnerStore = create<RunnerState>((set, get) => ({
  runners: [],
  selectedId: "native",
  loading: false,
  starting: false,
  error: null,
  lastStart: null,

  setSelected: (selectedId) => set({ selectedId, error: null }),

  list: async () => {
    set({ loading: true, error: null });
    try {
      const result = await typedIPC.listRunners();
      set((state) => ({
        runners: result.runners,
        selectedId: result.runners.some((runner) => runner.id === state.selectedId)
          ? state.selectedId
          : result.runners[0]?.id ?? "native",
        loading: false,
      }));
    } catch (err) {
      set({ loading: false, error: err instanceof Error ? err.message : String(err) });
    }
  },

  start: async (opts) => {
    const command = opts.command.trim();
    if (!command) return null;
    set({ starting: true, error: null });
    try {
      const result = await typedIPC.startRunner({
        runner_id: get().selectedId,
        command,
        cwd: opts.cwd,
        session_id: opts.session_id,
        timeout_s: opts.timeout_s,
        sandbox_mode: opts.sandbox_mode,
        approval_policy: opts.approval_policy,
        permission_mode: opts.permission_mode,
      });
      set({ starting: false, lastStart: result });
      return result;
    } catch (err) {
      set({ starting: false, error: err instanceof Error ? err.message : String(err) });
      return null;
    }
  },

  reset: () =>
    set({
      runners: [],
      selectedId: "native",
      loading: false,
      starting: false,
      error: null,
      lastStart: null,
    }),
}));

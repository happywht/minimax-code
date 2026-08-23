/**
 * Permission store — "always allow" toggle + per-tool rules + in-flight
 * consent requests.
 *
 * The store is the single source of truth for the consent flow:
 *
 *   1. The Python sidecar emits `permission.request` (carrying
 *      request_id, tool, args) when an `ask`-rule tool is about to
 *      run.
 *   2. We push the request into `pending` keyed by `request_id` and
 *      surface a modal so the user can decide.
 *   3. The user clicks 允许/拒绝 → the store calls
 *      `typedIPC.resolvePermission({ request_id, decision })` and
 *      removes the entry from `pending`.
 *   4. The sidecar unblocks its agent loop and continues / aborts
 *      the tool call.
 *
 * The `alwaysAllow` shortcut still works — when true, incoming
 * `permission.request` events are auto-resolved with `decision:
 * "allow"` so the modal never has to appear.
 */

import { create } from "zustand";
import { ipc, typedIPC } from "../ipc";
import { toast } from "../components/layout/ErrorBoundary";
import type {
  PermissionRequestData,
  PermissionResolvedData,
  PermissionRule,
} from "../types/ipc";
import { StreamEvent } from "../types/ipc";
import { strings } from "../ui/strings";

export type PermissionRuleEntry = PermissionRule;

export type Decision = "allow" | "deny";

export interface PendingPermission {
  request_id: string;
  tool: string;
  args: Record<string, unknown>;
  /** v1.2.0 — which session asked (parallel prompts used to be indistinguishable). */
  session_id?: string;
  /** Wall-clock timestamp (Date.now()) the request arrived. */
  received_at: number;
}

export interface PermissionState {
  alwaysAllow: boolean;
  rules: PermissionRuleEntry[];
  loading: boolean;
  /** Active consent prompts keyed by request_id. */
  pending: Record<string, PendingPermission>;
  /** Decisions currently being posted to the Agent. */
  resolving: Record<string, Decision>;

  refresh: () => Promise<void>;
  setAlwaysAllow: (v: boolean) => void;
  upsertRule: (
    rule: Omit<PermissionRule, "id" | "created_at"> & { id?: string },
  ) => Promise<void>;
  removeRule: (id: string) => Promise<void>;

  /** Internal — called by the IPC `permission.request` listener. */
  _enqueueRequest: (data: PermissionRequestData) => void;
  /** Internal — called by the IPC `permission.resolved` listener. */
  _markResolved: (data: PermissionResolvedData) => void;
  /** User-driven resolve: POSTs to the sidecar and clears local state. */
  resolve: (request_id: string, decision: Decision) => Promise<void>;
}

let reqUnsub: (() => void) | null = null;
let resUnsub: (() => void) | null = null;

function ensureListeners(): void {
  if (reqUnsub || resUnsub) return;
  if (typeof window === "undefined") return;
  // The IPCClient singleton is the source of events; we wire the
  // listeners once and let the store react. We never re-subscribe on
  // every component mount.
  try {
    reqUnsub = ipc.on<PermissionRequestData>(StreamEvent.PermissionRequest, (env) => {
      const data = env.data;
      if (!data) return;
      const state = usePermissionStore.getState();
      // "always allow" bypass — auto-allow without showing UI.
      if (state.alwaysAllow) {
        state._enqueueRequest(data);
        void state.resolve(data.request_id, "allow").catch(() => undefined);
        return;
      }
      usePermissionStore.setState((s) => ({
        pending: {
          ...s.pending,
          [data.request_id]: {
            request_id: data.request_id,
            tool: data.tool,
            args: data.args,
            session_id: data.session_id,
            received_at: Date.now(),
          },
        },
      }));
    });
    resUnsub = ipc.on<PermissionResolvedData>(
      StreamEvent.PermissionResolved,
      (env) => {
        const data = env.data;
        if (!data) return;
        usePermissionStore.setState((s) => {
          if (!s.pending[data.request_id] && !s.resolving[data.request_id]) return {};
          const next = { ...s.pending };
          const resolving = { ...s.resolving };
          delete next[data.request_id];
          delete resolving[data.request_id];
          return { pending: next, resolving };
        });
      },
    );
  } catch {
    // ipc.on may throw in unit tests where the client is constructed
    // late; that's fine — the modal will still work via direct calls.
  }
}

export const usePermissionStore = create<PermissionState>((set, get) => ({
  alwaysAllow: false,
  rules: [],
  loading: false,
  pending: {},
  resolving: {},

  refresh: async () => {
    set({ loading: true });
    try {
      const r = await typedIPC.listRules();
      set({ rules: r.rules, loading: false });
    } catch (err) {
      set({ loading: false });
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.rulesLoadFailed, message);
    }
  },

  setAlwaysAllow: (v: boolean) => set({ alwaysAllow: v }),

  upsertRule: async (rule) => {
    try {
      const r = await typedIPC.setRule(rule);
      const existing = get().rules;
      const idx = existing.findIndex((x) => x.id === r.rule.id);
      const next =
        idx === -1
          ? [...existing, r.rule]
          : existing.map((x) => (x.id === r.rule.id ? r.rule : x));
      set({ rules: next });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.ruleSaveFailed, message);
    }
  },

  removeRule: async (id: string) => {
    try {
      // Backend `permission.delete` expects `tool_pattern`, not DB id.
      const rule = get().rules.find((x) => x.id === id);
      await typedIPC.deleteRule(rule?.tool ?? id);
      set((s) => ({ rules: s.rules.filter((x) => x.id !== id) }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.ruleDeleteFailed, message);
    }
  },

  _enqueueRequest: (data) => {
    set((s) => ({
      pending: {
        ...s.pending,
        [data.request_id]: {
          request_id: data.request_id,
          tool: data.tool,
          args: data.args,
          received_at: Date.now(),
        },
      },
    }));
  },

  _markResolved: (data) => {
    set((s) => {
      if (!s.pending[data.request_id] && !s.resolving[data.request_id]) return {};
      const next = { ...s.pending };
      const resolving = { ...s.resolving };
      delete next[data.request_id];
      delete resolving[data.request_id];
      return { pending: next, resolving };
    });
  },

  resolve: async (request_id, decision) => {
    if (get().resolving[request_id]) return;
    set((s) => ({ resolving: { ...s.resolving, [request_id]: decision } }));
    try {
      await typedIPC.resolvePermission({ request_id, decision });
      set((s) => {
        const pending = { ...s.pending };
        const resolving = { ...s.resolving };
        delete pending[request_id];
        delete resolving[request_id];
        return { pending, resolving };
      });
    } catch (err) {
      set((s) => {
        const resolving = { ...s.resolving };
        delete resolving[request_id];
        return { resolving };
      });
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.permissionResolveFailed, message);
    }
  },
}));

// Wire listeners on module import — they're idempotent and safe to
// call from a browser without a Tauri shell.
ensureListeners();

/** Test seam — clears the cached listener unsubs so a new
 * subscription can be set up. */
export function _resetPermissionStoreListeners(): void {
  reqUnsub?.();
  resUnsub?.();
  reqUnsub = null;
  resUnsub = null;
}

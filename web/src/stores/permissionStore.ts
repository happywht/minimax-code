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
  approval_policy?: "explicit_once";
  request_id: string;
  tool: string;
  args: Record<string, unknown>;
  /** v1.2.0 — which session asked (parallel prompts used to be indistinguishable). */
  session_id?: string;
  run_id?: string;
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
  recoveryVersion: (sessionId: string) => number;
  reconcilePermissions: (sessionId: string, runIds: string[], native: PermissionRequestData[] | null,
    codex: PermissionRequestData[] | null, version: number) => void;
  /** User-driven resolve: POSTs to the sidecar and clears local state. */
  resolve: (request_id: string, decision: Decision) => Promise<void>;
}

let reqUnsub: (() => void) | null = null;
let resUnsub: (() => void) | null = null;
let closedUnsub: (() => void) | null = null;
const versions = new Map<string, number>();
function bump(sessionId?: string): void {
  if (sessionId) versions.set(sessionId, (versions.get(sessionId) ?? 0) + 1);
}
const closedIds = new Set<string>();
const timers = new Map<string, ReturnType<typeof setTimeout>>();

function retire(requestId: string): void {
  bump(usePermissionStore.getState().pending[requestId]?.session_id);
  closedIds.add(requestId);
  const timer = timers.get(requestId);
  if (timer) clearTimeout(timer);
  timers.delete(requestId);
  usePermissionStore.setState((state) => {
    const pending = { ...state.pending }, resolving = { ...state.resolving };
    delete pending[requestId]; delete resolving[requestId];
    return { pending, resolving };
  });
}

function enqueue(data: PermissionRequestData): void {
  if (closedIds.has(data.request_id)) return;
  if (data.remaining_ms !== undefined && data.remaining_ms <= 0) { retire(data.request_id); return; }
  const existing = usePermissionStore.getState().pending[data.request_id];
  if (existing) return;
  bump(data.session_id);
  usePermissionStore.setState((state) => ({ pending: { ...state.pending, [data.request_id]: {
    ...data, received_at: Date.now(),
  } } }));
  if (data.remaining_ms !== undefined) timers.set(data.request_id, setTimeout(() => retire(data.request_id), data.remaining_ms));
}

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
      if (closedIds.has(data.request_id)) return;
      const state = usePermissionStore.getState();
      // Native convenience settings cannot grant external-engine approvals.
      if (state.alwaysAllow && !data.tool.startsWith("codex:") && data.approval_policy !== "explicit_once") {
        state._enqueueRequest(data);
        void state.resolve(data.request_id, "allow").catch(() => undefined);
        return;
      }
      enqueue(data);
    });
    resUnsub = ipc.on<PermissionResolvedData>(
      StreamEvent.PermissionResolved,
      (env) => {
        const data = env.data;
        if (!data) return;
        retire(data.request_id);
      },
    );
    closedUnsub = ipc.on<{ request_id: string }>(StreamEvent.PermissionClosed, (env) => {
      if (env.data?.request_id) retire(env.data.request_id);
    });
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
  recoveryVersion: (sessionId) => versions.get(sessionId) ?? 0,
  reconcilePermissions: (sessionId, runIds, native, codex, version) => {
    if (version !== (versions.get(sessionId) ?? 0)) return;
    const requests = [...(native ?? []), ...(codex ?? [])];
    const active = new Set(requests.map((request) => request.request_id));
    for (const item of Object.values(get().pending)) {
      const supported = item.tool.startsWith("codex:") ? codex !== null : native !== null;
      if (item.session_id === sessionId && item.run_id && runIds.includes(item.run_id)
          && supported && !active.has(item.request_id)) retire(item.request_id);
    }
    requests.filter((item) => item.session_id === sessionId && item.run_id)
      .forEach(enqueue);
  },

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
    enqueue(data);
  },

  _markResolved: (data) => {
    retire(data.request_id);
  },

  resolve: async (request_id, decision) => {
    if (get().resolving[request_id]) return;
    const request = get().pending[request_id];
    if (!request) return;
    const host = ipc.agentBaseUrl;
    bump(request.session_id);
    set((s) => ({ resolving: { ...s.resolving, [request_id]: decision } }));
    try {
      let scope: { session_id: string; run_id: string; expected_service_id: string } | undefined;
      if (request.session_id && request.run_id) {
        const identity = await typedIPC.getServiceIdentity();
        if (!identity.service_id || ipc.agentBaseUrl !== host || get().pending[request_id] !== request)
          throw new Error("授权请求或连接已变化，请重新核对。");
        scope = { session_id: request.session_id, run_id: request.run_id, expected_service_id: identity.service_id };
      }
      const result = await typedIPC.resolvePermission({ request_id, decision, ...scope });
      retire(request_id);
      if (!result.ok) {
        toast.error(strings.toasts.permissionResolveFailed,
          result.reason ?? "请求已失效，决定未被接受；请查看运行状态。 ");
      }
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
  closedUnsub?.();
  reqUnsub = null;
  resUnsub = null;
  closedUnsub = null;
  timers.forEach(clearTimeout); timers.clear(); closedIds.clear(); versions.clear();
}

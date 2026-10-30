/**
 * Chat store — owns the streaming message log and the send() loop.
 *
 * State machine:
 *   idle → sending → streaming → idle
 *   idle → sending → error
 *   streaming → cancelling → idle
 *
 * The store wires the IPC `agent.message_chunk` event into a streaming
 * assistant bubble. Tool-call/tool-result events are appended as
 * additional message bubbles so the user can see intermediate steps.
 */

import { create } from "zustand";
import { ipc, IPCError, typedIPC } from "../ipc";
import { toast } from "../components/layout/ErrorBoundary";
import {
  StreamEvent,
  type AgentStatusData,
  type AskUserData,
  type MessageChunkData,
  type ToolCallData,
  type ToolResultData,
} from "../types/ipc";
import type { Message, ContentPart, AgentRun, SendMessageResult, MessageStatus, RunStepsResult } from "../types/ipc";
import { useSessionStore } from "./sessionStore";
import { trimArray, MAX_MESSAGES } from "../lib/eviction";
import { DEFAULT_SESSION_TITLE } from "../lib/defaultTitles";
import { strings } from "../ui/strings";

export type ChatStatus = "idle" | "sending" | "streaming" | "error" | "cancelling";

function receiptMessageStatus(result: SendMessageResult): MessageStatus {
  const status = result.run_status;
  if (status === "cancelled" || status === "failed" || status === "interrupted" || status === "budget_paused") return status;
  return result.cancelled ? "cancelled" : "completed";
}

export interface ChatState {
  captureFileChanges: boolean;
  setCaptureFileChanges: (enabled: boolean) => void;
  executionMode: "execute" | "plan";
  setExecutionMode: (mode: "execute" | "plan") => void;
  messages: Message[];
  status: ChatStatus;
  error: string | null;
  agentReady: boolean;
  /** True while loadMessages is fetching history for the current session. */
  loadingMessages: boolean;
  /**
   * Pending `ask_user` questionnaire — the agent is suspended until the
   * user answers (or the backend 600 s timeout fires). Null when idle.
   */
  askUser: AskUserData | null;
  askRecoveryVersion: () => number;
  reconcileAskUser: (request: AskUserData | null, version: number, sessionId: string) => void;

  init: () => Promise<void>;
  send: (content: string | ContentPart[], budgetTokens?: number, restrictNetworkTools?: boolean) => Promise<void>;
  /**
   * v1.1.0 — resume the latest budget-truncated run. Unlike send(),
   * no user bubble is added: the backend injects a fixed continuation
   * prompt server-side and streams the new assistant turn via the
   * regular message_chunk channel.
   */
  continueRun: (runId?: string) => Promise<void>;
  /** Add a user message to the chat log without triggering a backend call. */
  addLocalMessage: (content: string) => void;
  /** Load persisted messages for a session from the backend. */
  loadMessages: (sessionId: string) => Promise<void>;
  reconcileRuns: (runs: AgentRun[], stillCurrent: () => boolean, includeActive?: boolean) => Promise<void>;
  reconcileActiveStatus: (sessionId: string, active: boolean) => void;
  restartHistoryOnReconnect: (sessionId: string) => void;
  reconcileLiveStream: (run: AgentRun, stream: NonNullable<RunStepsResult["live_stream"]>) => void;
  reset: () => void;
  cancel: () => Promise<void>;
  retryMessage: (messageId: string) => Promise<void>;
  updateMessage: (messageId: string, text: string) => Promise<void>;
  deleteMessage: (messageId: string) => Promise<void>;
  /**
   * Answer the pending ask_user questionnaire. One entry per question
   * (positionally aligned); a multi-select answer is an array of labels.
   * The card stays up on transport errors so the user can retry, and is
   * cleared once the backend accepts or explicitly rejects the answer.
   */
  submitAskUser: (answers: Array<string | string[]>) => Promise<void>;
  /** Decline to answer — replies "(skipped by user)" for every question. */
  skipAskUser: () => Promise<void>;
}

let chunkUnsub: (() => void) | null = null;
let toolCallUnsub: (() => void) | null = null;
let toolResultUnsub: (() => void) | null = null;
let statusUnsub: (() => void) | null = null;
let askUserUnsub: (() => void) | null = null;
let askUserClosedUnsub: (() => void) | null = null;
let pendingAssistantId: string | null = null;
let pendingActiveSession: string | null = null;
let deferredStreamSession: string | null = null;
const inFlightRequests = new Map<string, Promise<unknown>>();

function trackRequest<T>(sessionId: string, request: Promise<T>): Promise<T> {
  const tracked = request.finally(() => {
    if (inFlightRequests.get(sessionId) === tracked) inFlightRequests.delete(sessionId);
  });
  inFlightRequests.set(sessionId, tracked);
  return tracked;
}

/** Tear down all WS subscriptions (HMR hot-replace / app unmount). */
export function disposeChatSubscriptions(): void {
  chunkUnsub?.();
  toolCallUnsub?.();
  toolResultUnsub?.();
  statusUnsub?.();
  askUserUnsub?.();
  askUserClosedUnsub?.();
  chunkUnsub = null;
  toolCallUnsub = null;
  toolResultUnsub = null;
  statusUnsub = null;
  askUserUnsub = null;
  askUserClosedUnsub = null;
}

// HMR cleanup — tear down WS listeners when this module is hot-replaced
// so stale subscriptions don't accumulate and cause duplicate event handling.
if (import.meta.hot) {
  import.meta.hot.dispose(disposeChatSubscriptions);
}

/** Monotonic counter to prevent stale loadMessages from overwriting state. */
let _loadSeq = 0;
let _viewRevision = 0;
let _reconcileSeq = 0;
let askVersion = 0;
let askExpiryTimer: ReturnType<typeof setTimeout> | null = null;
const closedAskIds = new Set<string>();

function setLiveQuestion(data: AskUserData | null) {
  if (askExpiryTimer) clearTimeout(askExpiryTimer);
  askExpiryTimer = null;
  if (data && data.remaining_ms !== undefined) {
    const remaining = data.remaining_ms;
    if (remaining <= 0) data = null;
    else {
      const id = data.request_id;
      askExpiryTimer = setTimeout(() => {
        ++askVersion; closedAskIds.add(id);
        if (useChat.getState().askUser?.request_id === id) useChat.setState({ askUser: null });
      }, remaining);
    }
  }
  useChat.setState({ askUser: data });
}
const recoveredMessageIds = new Set<string>();
const recoveredRunIds = new Set<string>();
let reconcilingRunIds = new Set<string>();
type StreamCursor = { seq: number; queued: Map<number, MessageChunkData> };
const streamCursors = new Map<string, StreamCursor>();
const localMessageEdits = new Set<string>();
const streamWarnings = new Set<string>();
const unavailableStreams = new Map<string, number>();
const streamKey = (runId: string, messageId: string) => JSON.stringify([runId, messageId]);

function warnStream(runId: string): void {
  if (streamWarnings.has(runId)) return;
  streamWarnings.add(runId);
  toast.info("流式内容无法完整补齐", "请等待任务结束后查看已保存的记录。");
}

function requestStreamSnapshot(sessionId: string): void {
  void import("./runStore").then(({ useRunTimelineStore }) => {
    if (useSessionStore.getState().currentSessionId === sessionId) void useRunTimelineStore.getState().loadForSession(sessionId);
  }).catch(() => warnStream(sessionId));
}

function applyStreamEvent(data: MessageChunkData): void {
  const runId = data.run_id;
  if (!runId || !data.session_id || !isCurrentSessionEvent(data) || recoveredMessageIds.has(data.message_id)) return;
  const key = streamKey(runId, data.message_id);
  const known = streamCursors.has(key);
  const cursor = streamCursors.get(key) ?? { seq: 0, queued: new Map<number, MessageChunkData>() };
  streamCursors.set(key, cursor);
  if (localMessageEdits.has(data.message_id)) return;
  if (data.stream_unavailable) {
    warnStream(runId);
    const seq = data.stream_event_seq;
    if (!Number.isSafeInteger(seq) || seq === undefined || seq <= (unavailableStreams.get(runId) ?? 0)) return;
    unavailableStreams.set(runId, seq);
    cursor.queued.clear();
  } else if (data.stream_seq !== undefined) {
    if (unavailableStreams.has(runId)) return;
    const seq = data.stream_seq;
    if (!Number.isSafeInteger(seq) || seq <= 0 || seq <= cursor.seq) return;
    const existing = useChat.getState().messages.find((message) => message.id === data.message_id);
    if (seq !== cursor.seq + 1 || (!known && !!existing?.text)) {
      if (cursor.queued.size < 1000) cursor.queued.set(seq, data);
      else warnStream(runId);
      requestStreamSnapshot(data.session_id);
      return;
    }
    cursor.seq = seq;
  }
  noteStreamActivity(data);
  useChat.setState((state) => {
    const awaiting = data.done && inFlightRequests.has(data.session_id!);
    const next = applyAssistantChunk(state.messages, data).map((message) => awaiting && message.id === data.message_id
      ? { ...message, streaming: true, status: "streaming" as const } : message);
    return { messages: trimArray(state.status === "cancelling"
      ? next.map((message) => message.id === data.message_id ? { ...message, status: "cancelling" as const } : message)
      : next, MAX_MESSAGES), status: state.status === "cancelling" ? "cancelling" : data.done && !awaiting ? "idle" : "streaming" };
  });
  if (data.done) { pendingAssistantId = null; clearStallWatchdog(); }
  else resetStallWatchdogForCurrentActivity();
  const next = cursor.queued.get(cursor.seq + 1);
  if (next) { cursor.queued.delete(cursor.seq + 1); applyStreamEvent(next); }
}

/** Frontend inactivity notice. Only the backend may terminate a run. */
const STALL_TIMEOUT_MS = 60_000;
/**
 * Backend tool dispatch defaults to 120 s. While a tool is actively
 * running, allow a wider silence window so long shell/test/search work
 * does not get misreported as a dead chat stream.
 */
const TOOL_STALL_TIMEOUT_MS = 180_000;
let _stallTimer: ReturnType<typeof setTimeout> | null = null;
const activeToolCalls = new Set<string>();
const activeToolNames = new Map<string, string>();

function resetStallWatchdog(timeoutMs = STALL_TIMEOUT_MS) {
  if (_stallTimer) clearTimeout(_stallTimer);
  _stallTimer = setTimeout(() => {
    _stallTimer = null;
    const s = useChat.getState();
    if (s.status === "streaming" || s.status === "sending") {
      const seconds = Math.round(timeoutMs / 1000);
      const detail =
        activeToolCalls.size > 0
          ? `No tool progress received for ${seconds} seconds. The current tool may have stalled.`
          : `No data received for ${seconds} seconds. The connection may have stalled.`;
      toast.info(strings.toasts.agentBusy, detail);
      resetStallWatchdogForCurrentActivity();
    }
  }, timeoutMs);
}

function resetStallWatchdogForCurrentActivity() {
  resetStallWatchdog(activeToolCalls.size > 0 ? TOOL_STALL_TIMEOUT_MS : STALL_TIMEOUT_MS);
}

function clearStallWatchdog() {
  if (_stallTimer) {
    clearTimeout(_stallTimer);
    _stallTimer = null;
  }
  activeToolCalls.clear();
  activeToolNames.clear();
}

/**
 * Session guard for chat-stream events. The backend fans every broadcast
 * out to every WebSocket client — skill.invoke, agent.invoke and runs in
 * other tabs all ride the same message_chunk/tool_call/status channel,
 * stamped with their own (temporary) session ids. Only events belonging
 * to the currently open session may touch this store; anything else is a
 * background run and must not pollute the visible conversation.
 */
function isCurrentSessionEvent(data: { session_id?: string | null; run_id?: string }): boolean {
  return (
    !!data.session_id &&
    !(data.run_id && recoveredRunIds.has(data.run_id)) &&
    data.session_id === useSessionStore.getState().currentSessionId
  );
}

function noteStreamActivity(data: { run_id?: string }): void {
  // A completed run's late packets are superseded by its committed history.
  // Activity from a different run must invalidate the pending history read.
  if (!data.run_id || !reconcilingRunIds.has(data.run_id)) ++_viewRevision;
}

/** Add saved rows around common anchors without replacing anything in the live view. */
function mergeHistory(saved: Message[], current: Message[], initiallyEmpty: boolean): { messages: Message[]; incomplete: boolean } {
  const match = (row: Message, existing: Message): boolean => {
    if (row.id === existing.id) return true;
    const a = row.metadata, b = existing.metadata;
    if (a?.run_id && b?.run_id && a.run_id !== b.run_id) return false;
    if (row.role === "user" && existing.role === "user" && a?.request_key && a.request_key === b?.request_key) return true;
    return row.role === "tool" && existing.role === "tool" && !!row.tool_call_id
      && row.tool_call_id === existing.tool_call_id && !existing.id.startsWith("tc-");
  };
  const anchors = saved.map((row) => current.findIndex((existing) => match(row, existing)));
  let last = -1;
  for (const index of anchors) {
    if (index < 0) continue;
    if (index <= last) return { messages: current, incomplete: true };
    last = index;
  }
  const hasAnchor = last >= 0;
  const lastSaved = saved[saved.length - 1];
  const firstCurrent = current[0];
  const historyPrecedesLive = initiallyEmpty && lastSaved?.role === "user"
    && !!lastSaved.metadata?.run_id && lastSaved.metadata.run_id === firstCurrent?.metadata?.run_id
    && firstCurrent.role === "assistant";
  const uncertainInput = current.some((row) => row.role === "user" && !!row.metadata?.request_key
    && !saved.some((item) => match(item, row)));
  const buckets = new Map<number, Message[]>();
  let previous = -1;
  let incomplete = false;
  const seen = new Set<string>();
  for (let i = 0; i < saved.length; i++) {
    const row = saved[i];
    if (anchors[i] >= 0) { previous = anchors[i]; continue; }
    if (seen.has(row.id) || localMessageEdits.has(row.id)) continue;
    seen.add(row.id);
    // An unconfirmed input and an unkeyed saved user row cannot be
    // identified as the same submission safely.
    const next = anchors.slice(i + 1).find((index) => index >= 0);
    const end = next ?? current.length;
    if ((!hasAnchor && current.length > 0 && !historyPrecedesLive)
        || (hasAnchor && end > previous + 1)
        || (uncertainInput && row.role === "user" && !row.metadata?.request_key)) {
      incomplete = true;
      continue;
    }
    const position = next ?? (previous >= 0 ? previous + 1 : 0);
    const bucket = buckets.get(position) ?? [];
    bucket.push(row);
    buckets.set(position, bucket);
  }
  const additions = [...buckets.values()].reduce((count, rows) => count + rows.length, 0);
  let skip = Math.max(0, additions - Math.max(0, MAX_MESSAGES - current.length));
  if (skip) incomplete = true;
  const merged: Message[] = [];
  for (let i = 0; i <= current.length; i++) {
    for (const row of buckets.get(i) ?? []) {
      if (skip > 0) skip--;
      else merged.push(row);
    }
    if (i < current.length) merged.push(current[i]);
  }
  return { messages: merged, incomplete };
}

function ensureMessage(
  messages: Message[],
  patch: Partial<Message> & { id: string },
): Message[] {
  if (messages.some((m) => m.id === patch.id)) {
    return messages.map((m) => (m.id === patch.id ? { ...m, ...patch } : m));
  }
  return [
    ...messages,
    {
      role: "assistant",
      text: "",
      streaming: false,
      status: patch.streaming ? "streaming" : "completed",
      created_at: Date.now(),
      ...patch,
    } as Message,
  ];
}

function applyAssistantChunk(
  messages: Message[],
  data: MessageChunkData,
): Message[] {
  const metadata = data.metadata;
  const delta = data.delta || "";
  const nextStatus = data.done ? "completed" : "streaming";
  const existing = messages.find((m) => m.id === data.message_id);

  if (existing) {
    return messages.map((m) =>
      m.id === data.message_id
        ? {
            ...m,
            text: m.text + delta,
            streaming: !data.done,
            status: nextStatus,
            ...(metadata ? { metadata } : {}),
          }
        : m,
    );
  }

  const pendingId = pendingAssistantId;
  if (data.done && !delta && pendingId) {
    const pending = messages.find((m) => m.id === pendingId);
    if (pending && pending.role === "assistant" && !pending.text) {
      return messages.filter((m) => m.id !== pendingId);
    }
  }

  if (pendingId && messages.some((m) => m.id === pendingId)) {
    return messages.map((m) =>
      m.id === pendingId
        ? {
            ...m,
            id: data.message_id,
            text: m.text + delta,
            streaming: !data.done,
            status: nextStatus,
            ...(metadata ? { metadata } : {}),
          }
        : m,
    );
  }

  return ensureMessage(messages, {
    id: data.message_id,
    role: "assistant",
    text: delta,
    streaming: !data.done,
    status: nextStatus,
    created_at: Date.now(),
    ...(metadata ? { metadata } : {}),
  });
}

export const useChat = create<ChatState>((set, get) => ({
  captureFileChanges: false,
  setCaptureFileChanges: (captureFileChanges) => set({ captureFileChanges }),
  executionMode: "execute",
  setExecutionMode: (executionMode) => set({ executionMode }),
  messages: [],
  status: "idle",
  error: null,
  agentReady: false,
  loadingMessages: false,
  askUser: null,
  askRecoveryVersion: () => askVersion,
  reconcileAskUser: (request, version, sessionId) => {
    if (askVersion !== version || useSessionStore.getState().currentSessionId !== sessionId) return;
    if (request && (request.session_id !== sessionId || closedAskIds.has(request.request_id))) return;
    if (get().askUser?.request_id === request?.request_id) return;
    ++askVersion;
    setLiveQuestion(request);
  },
  reconcileLiveStream: (run, stream) => {
    if (run.session_id !== useSessionStore.getState().currentSessionId) return;
    if (get().loadingMessages) {
      deferredStreamSession = run.session_id;
      return;
    }
    if (!stream.complete) { if (!unavailableStreams.has(run.id)) unavailableStreams.set(run.id, 0); warnStream(run.id); return; }
    if (unavailableStreams.has(run.id)) return;
    const replay: MessageChunkData[] = [];
    for (const [segmentIndex, segment] of stream.messages.entries()) {
      if (!segment.message_id || !Number.isSafeInteger(segment.seq) || segment.seq <= 0
          || localMessageEdits.has(segment.message_id)) continue;
      const key = streamKey(run.id, segment.message_id);
      const cursor = streamCursors.get(key) ?? { seq: 0, queued: new Map<number, MessageChunkData>() };
      streamCursors.set(key, cursor);
      if (segment.seq < cursor.seq) continue;
      const existing = get().messages.find((message) => message.id === segment.message_id);
      if (existing?.metadata?.run_id && existing.metadata.run_id !== run.id) continue;
      const placeholder = get().messages.find((message) => message.id === pendingAssistantId && message.metadata?.request_key
        && message.metadata.request_key === run.request_key);
      if (!existing && !placeholder && get().messages.length >= MAX_MESSAGES) { warnStream(run.id); continue; }
      cursor.seq = segment.seq;
      for (const seq of cursor.queued.keys()) if (seq <= cursor.seq) cursor.queued.delete(seq);
      set((state) => {
        const pending = pendingAssistantId ? state.messages.find((message) => message.id === pendingAssistantId) : undefined;
        const canReplace = !existing && pending && pending.metadata?.request_key && pending.metadata.request_key === run.request_key;
        const row: Message = { id: segment.message_id, role: "assistant", text: segment.text,
          created_at: existing?.created_at ?? pending?.created_at ?? Date.now(), streaming: !segment.done,
          status: segment.done ? "completed" : "streaming", metadata: { ...existing?.metadata, run_id: run.id } };
        let messages = state.messages;
        if (existing) messages = messages.map((message) => message.id === segment.message_id ? { ...message, ...row } : message);
        else if (canReplace) {
          messages = messages.map((message) => message.id === pendingAssistantId ? { ...row, render_key: message.render_key ?? message.id } : message);
          pendingAssistantId = segment.message_id;
        } else if (state.messages.length < MAX_MESSAGES) {
          const laterIds = new Set(stream.messages.slice(segmentIndex + 1).map((item) => item.message_id));
          const index = state.messages.findIndex((message) => laterIds.has(message.id)
            || (message.role === "tool" && message.parent_id === segment.message_id)
            || (message.role === "user" && !!message.metadata?.request_key && message.metadata.request_key !== run.request_key));
          const at = index < 0 ? state.messages.length : index;
          messages = [...state.messages.slice(0, at), row, ...state.messages.slice(at)];
        }
        return { messages, ...(!segment.done && state.status === "idle" ? { status: "streaming" as const } : {}) };
      });
      const next = cursor.queued.get(cursor.seq + 1);
      if (next) { cursor.queued.delete(cursor.seq + 1); replay.push(next); }
    }
    replay.forEach(applyStreamEvent);
  },

  reconcileActiveStatus: (sessionId, active) => {
    if (useSessionStore.getState().currentSessionId !== sessionId) return;
    pendingActiveSession = active && get().loadingMessages ? sessionId : null;
    if (active && !get().loadingMessages && get().status === "idle") set({ status: "streaming" });
  },

  restartHistoryOnReconnect: (sessionId) => {
    if (useSessionStore.getState().currentSessionId === sessionId && get().loadingMessages) {
      // A previous connection's response must not populate the new service view.
      void get().loadMessages(sessionId);
    }
  },

  init: async () => {
    if (get().agentReady) return;
    await ipc.start();

    if (!chunkUnsub) {
      chunkUnsub = ipc.on<MessageChunkData>(StreamEvent.MessageChunk, (env) => {
        const data = env.data;
        if (!data) return;
        if (!isCurrentSessionEvent(data)) return;
        if (recoveredMessageIds.has(data.message_id)) return;
        if (data.stream_seq !== undefined || data.stream_unavailable) { applyStreamEvent(data); return; }
        noteStreamActivity(data);
        set((s) => {
          // The v0.3.0 thinking_count channel attaches a
          // ``metadata`` field to the final ``done=True`` chunk
          // (sometimes also the first chunk). Replace, don't
          // sum — the wire spec says it's a snapshot, not a
          // counter. We only assign when the field is present
          // so an earlier chunk's metadata isn't clobbered by a
          // later one that omits it.
          const awaitingReceipt = data.done && inFlightRequests.has(data.session_id ?? useSessionStore.getState().currentSessionId ?? "");
          const next = applyAssistantChunk(s.messages, data).map((m) => awaitingReceipt && m.id === data.message_id
            ? { ...m, streaming: true, status: "streaming" as const } : m);
          return {
            messages: trimArray(s.status === "cancelling"
              ? next.map((m) => m.id === data.message_id ? { ...m, status: "cancelling" as const } : m)
              : next, MAX_MESSAGES),
            status: s.status === "cancelling" ? "cancelling" : data.done && !awaitingReceipt ? "idle" : "streaming",
          };
        });
        // Stall watchdog: reset on every chunk, clear on done.
        if (data.done) {
          pendingAssistantId = null;
          clearStallWatchdog();
        } else {
          resetStallWatchdogForCurrentActivity();
        }
      });
    }
    if (!toolCallUnsub) {
      toolCallUnsub = ipc.on<ToolCallData>(StreamEvent.ToolCall, (env) => {
        const data = env.data;
        if (!data) return;
        if (!isCurrentSessionEvent(data)) return;
        const id = `tc-${data.tool_call_id}`;
        noteStreamActivity(data);
        set((s) => ({
          messages: trimArray(ensureMessage(s.messages, {
            id,
            role: "tool",
            text: `🔧 ${data.name}(${JSON.stringify(data.args)})`,
            tool_call_id: data.tool_call_id,
            tool_name: data.name,
            tool_args: data.args,
            parent_id: data.message_id,
            created_at: Date.now(),
            streaming: false,
          }), MAX_MESSAGES),
          status: s.status === "cancelling" ? "cancelling" : "streaming",
        }));
        // Tool execution is activity. It can legitimately run longer
        // than the normal 60 s stream silence window, so arm the wider
        // tool watchdog until a matching tool_result arrives.
        activeToolCalls.add(data.tool_call_id);
        activeToolNames.set(data.tool_call_id, data.name);
        resetStallWatchdogForCurrentActivity();
      });
    }
    if (!toolResultUnsub) {
      toolResultUnsub = ipc.on<ToolResultData>(StreamEvent.ToolResult, (env) => {
        const data = env.data;
        if (!data) return;
        if (!isCurrentSessionEvent(data)) return;
        const id = `tr-${data.tool_call_id}`;
        noteStreamActivity(data);
        const text = data.error
          ? `✗ ${data.error}`
          : `✓ ${typeof data.result === "string" ? data.result : JSON.stringify(data.result)}`;
        const toolName = data.name ?? activeToolNames.get(data.tool_call_id);
        if (!recoveredMessageIds.has(id)) set((s) => ({
          messages: trimArray(ensureMessage(s.messages, {
            id,
            role: "tool",
            text,
            tool_call_id: data.tool_call_id,
            tool_name: toolName,
            parent_id: `tc-${data.tool_call_id}`,
            created_at: Date.now(),
            streaming: false,
          }), MAX_MESSAGES),
        }));
        // Tool result is activity. Once all active tools have returned,
        // go back to the normal stream watchdog window.
        activeToolCalls.delete(data.tool_call_id);
        activeToolNames.delete(data.tool_call_id);
        resetStallWatchdogForCurrentActivity();
        // An ask_user tool result means the suspension is over — the
        // answer was accepted, timed out, or the run was cancelled.
        // Either way the pending card must not linger (timeout/cancel
        // never route through submitAskUser).
        if (toolName === "ask_user") {
          const current = get().askUser;
          if (current && (!data.run_id || !current.run_id || data.run_id === current.run_id)
              && (!current.tool_call_id || data.tool_call_id === current.tool_call_id)) {
            ++askVersion; closedAskIds.add(current.request_id); setLiveQuestion(null);
          }
        }
      });
    }
    if (!statusUnsub) {
      statusUnsub = ipc.on<AgentStatusData>(StreamEvent.AgentStatus, (env) => {
        const d = env.data;
        if (!d) return;
        if (!isCurrentSessionEvent(d)) return;
        noteStreamActivity(d);
        if (d.status === "error") {
          const detail = d.detail ?? "Agent run failed";
          set((s) => ({
            status: "error",
            error: detail,
            messages: s.messages.map((message) =>
              message.role === "assistant" && message.streaming
                ? {
                    ...message,
                    text: message.text || detail,
                    streaming: false,
                    status: "failed",
                    error: detail,
                  }
                : message,
            ),
          }));
          pendingAssistantId = null;
          toast.error(strings.toasts.agentError, detail);
          clearStallWatchdog();
        } else if (d.status === "idle" || d.status === "done" || d.status === "max_iterations") {
          if (get().status !== "cancelling") set({ status: "idle" });
          clearStallWatchdog();
        } else {
          resetStallWatchdogForCurrentActivity();
        }
      });
    }

    if (!askUserUnsub) {
      askUserUnsub = ipc.on<AskUserData>(StreamEvent.AskUser, (env) => {
        const data = env.data;
        if (!data) return;
        if (!isCurrentSessionEvent(data)) return;
        if (closedAskIds.has(data.request_id)) return;
        // Concurrent descendants can ask at once. Keep the displayed
        // question stable; the run snapshot surfaces the next on close.
        if (get().askUser && get().askUser?.request_id !== data.request_id) return;
        noteStreamActivity(data);
        ++askVersion;
        setLiveQuestion(data);
      });
    }
    if (!askUserClosedUnsub) {
      askUserClosedUnsub = ipc.on<{ session_id: string; run_id?: string; request_id: string }>(StreamEvent.AskUserClosed, (env) => {
        const data = env.data;
        if (!data || data.session_id !== useSessionStore.getState().currentSessionId) return;
        ++askVersion; closedAskIds.add(data.request_id);
        if (get().askUser?.request_id === data.request_id) setLiveQuestion(null);
        // Other children may already be waiting. Their earlier events may
        // have been ignored while this card was visible, so fetch a fresh
        // live snapshot after every close. The session check prevents a late
        // import from refreshing a conversation the user has left.
        const sessionId = data.session_id;
        void import("./runStore").then(({ useRunTimelineStore }) => {
          if (useSessionStore.getState().currentSessionId === sessionId) {
            void useRunTimelineStore.getState().loadForSession(sessionId);
          }
        });
      });
    }

    const ready = await ipc.ping();
    set({ agentReady: ready });
  },

  addLocalMessage: (content: string) => {
    const text = content.trim();
    if (!text) return;
    ++_viewRevision;
    set((s) => ({
      messages: trimArray([
        ...s.messages,
        {
          id: `user-${Date.now()}`,
          role: "user",
          text,
          metadata: { request_key: crypto.randomUUID() },
          streaming: false,
          status: "completed",
          created_at: Date.now(),
        },
      ], MAX_MESSAGES),
    }));
  },

  send: async (content: string | ContentPart[], budgetTokens?: number, restrictNetworkTools = false) => {
    if (budgetTokens !== undefined && (!Number.isSafeInteger(budgetTokens) || budgetTokens <= 0)) {
      set({ error: "任务 token 上限必须是安全范围内的正整数。" }); return;
    }
    // Concurrency guard — prevent double-send from rapid clicks or
    // multiple call sites.  The UI disables the button but the store
    // is the source of truth.
    if (get().status === "sending" || get().status === "streaming" || get().status === "cancelling") return;

    // Normalize to wire format
    const isString = typeof content === "string";
    const text = isString ? (content as string).trim() : "";
    const parts = !isString ? (content as ContentPart[]) : undefined;

    // Build display text for the local user message bubble
    let displayText = text;
    if (parts) {
      const textParts = parts.filter((p) => p.type === "text").map((p) => p.text);
      const imgCount = parts.filter((p) => p.type === "image").length;
      displayText = textParts.join(" ") + (imgCount > 0 ? ` [${imgCount} image${imgCount > 1 ? "s" : ""}]` : "");
      displayText = displayText.trim();
    }

    if (!displayText && !parts?.length) return;

    ++_viewRevision;
    activeToolCalls.clear();
    activeToolNames.clear();
    set({ status: "sending", error: null });
    let sessionId = useSessionStore.getState().currentSessionId;
    try {
      if (!sessionId) {
        const title =
          displayText.replace(/\s+/g, " ").trim().slice(0, 60) || DEFAULT_SESSION_TITLE;
        sessionId = await useSessionStore.getState().create(title);
      } else {
        const sessions = useSessionStore.getState();
        const current = sessions.sessions.find((session) => session.id === sessionId);
        if (current?.title === DEFAULT_SESSION_TITLE && get().messages.length === 0) {
          const title =
            displayText.replace(/\s+/g, " ").trim().slice(0, 60) || DEFAULT_SESSION_TITLE;
          await sessions.rename(sessionId, title);
        }
      }

      const pinned = useSessionStore.getState().sessions.find((session) => session.id === sessionId)?.engine ?? "native";
      if (pinned === "codex" && restrictNetworkTools) throw new Error("工具联网限制仅支持 Native 任务。");
      if (pinned === "codex" && parts) throw new Error("Codex 当前仅支持纯文本，请移除附件后重试。");
      const requestKey = crypto.randomUUID();
      const userMessage: Message = {
        id: `user-${Date.now()}`,
        role: "user",
        text: displayText,
        streaming: false,
        status: "completed",
        created_at: Date.now(),
        metadata: { request_key: requestKey },
      };
      const assistantPlaceholderId = `assistant-pending-${Date.now()}`;
      pendingAssistantId = assistantPlaceholderId;
      const assistantPlaceholder: Message = {
        id: assistantPlaceholderId,
        role: "assistant",
        text: "",
        streaming: true,
        status: "queued",
        retry_content: parts ? displayText : text,
        created_at: Date.now(),
        metadata: { request_key: requestKey },
      };
      set((s) => ({
        messages: trimArray([...s.messages, userMessage, assistantPlaceholder], MAX_MESSAGES),
      }));
      resetStallWatchdogForCurrentActivity();
      // Send string for pure text, list for multimodal
      const wireContent = parts ?? text;
      if (pinned === "codex") {
        const admission = await trackRequest(sessionId, typedIPC.startRun({
          session_id: sessionId, engine: "codex", content: wireContent,
          idempotency_key: requestKey, mode: get().executionMode,
          ...(budgetTokens !== undefined ? { budget_tokens: budgetTokens } : {}),
          ...(get().captureFileChanges ? { capture_file_changes: true } : {}),
        }));
        if (useSessionStore.getState().currentSessionId !== sessionId) return;
        const run = admission.run;
        pendingAssistantId = run.assistant_message_id ?? null;
        set((state) => ({ status: "streaming", messages: state.messages.map((message) =>
          message.id === userMessage.id ? { ...message, id: run.user_message_id ?? message.id,
            render_key: message.render_key ?? message.id, metadata: { ...message.metadata, run_id: run.id, engine: "codex" } }
          : message.id === assistantPlaceholderId ? { ...message, id: run.assistant_message_id ?? message.id,
            render_key: message.render_key ?? message.id, metadata: { ...message.metadata, run_id: run.id, engine: "codex" } }
          : message) }));
        const { useRunTimelineStore } = await import("./runStore");
        await useRunTimelineStore.getState().loadForSession(sessionId);
        return;
      }
      const result = await trackRequest(sessionId, typedIPC.sendMessage({
        session_id: sessionId,
        content: wireContent,
        idempotency_key: requestKey,
        ...(restrictNetworkTools ? { restrict_network_tools: true } : {}),
        ...(budgetTokens !== undefined ? { budget_tokens: budgetTokens } : {}),
        ...(get().captureFileChanges ? { capture_file_changes: true } : {}),
        ...(get().executionMode === "plan" ? { mode: "plan" as const } : {}),
      }));
      if (result.session_id && result.session_id !== sessionId) {
        const sessions = useSessionStore.getState();
        sessions.setCurrent(result.session_id, false);
        await sessions.refresh({ loadCurrent: false });
      }
      // Session-switch race guard — the user may have opened another
      // session while this HTTP reply was in flight. Chunks for the old
      // session were already dropped by the subscription guard; the
      // fallback below must not write the old session's reply into the
      // newly opened conversation either.
      if (useSessionStore.getState().currentSessionId !== sessionId) {
        pendingAssistantId = null;
        return;
      }
      clearStallWatchdog();
      if (result.cancelled) {
        const pending = pendingAssistantId;
        set((s) => ({
          status: "idle",
          messages: s.messages.map((m) =>
            m.id === result.message_id || m.id === pending
              ? { ...m, streaming: false, status: "cancelled", text: m.text || result.text || "Stopped.",
                  metadata: { ...m.metadata, run_id: result.run_id, status: result.run_status, partial: true, verification: result.verification } }
              : m,
          ),
        }));
        pendingAssistantId = null;
        activeToolCalls.clear();
        activeToolNames.clear();
        return;
      }
      // The HTTP response arrives *after* all WebSocket chunk events
      // have been processed (the agent streams chunks via WS, then
      // sends the JSON-RPC reply).  If the message was already
      // populated by chunk events, we must NOT overwrite the
      // accumulated text with "" — doing so would erase everything
      // the user just saw stream in.  Only create a fallback message
      // when no chunks were received (e.g. mock mode timing edge).
      set((s) => {
        const existing = s.messages.find((m) => m.id === result.message_id);
        if (existing) {
          // Already populated via WebSocket chunks — finalise status.
          return {
            messages: s.messages.map((m) =>
                  m.id === result.message_id ? { ...m, status: receiptMessageStatus(result), streaming: false,
                    metadata: { ...m.metadata, run_id: result.run_id, status: result.run_status, verification: result.verification } } : m,
                ),
            status: "idle",
          };
        }
        const pending = pendingAssistantId;
        if (pending && s.messages.some((m) => m.id === pending)) {
          return {
            messages: trimArray(s.messages.map((m) =>
              m.id === pending
                ? {
                    ...m,
                    id: result.message_id,
                    text: result.text || "",
                    streaming: false,
                    status: receiptMessageStatus(result),
                    metadata: { ...m.metadata, run_id: result.run_id, status: result.run_status, verification: result.verification },
                  }
                : m,
            ), MAX_MESSAGES),
            status: "idle",
          };
        }
        // No chunks received — create message from the full reply text.
        return {
          messages: trimArray(ensureMessage(s.messages, {
            id: result.message_id,
            role: "assistant",
            text: result.text || "",
            streaming: false,
            status: receiptMessageStatus(result),
            created_at: Date.now(),
            metadata: { run_id: result.run_id, status: result.run_status, verification: result.verification },
          }), MAX_MESSAGES),
          status: "idle",
        };
      });
      pendingAssistantId = null;
    } catch (err) {
      const message =
        err instanceof IPCError
          ? err.message
          : err instanceof Error
            ? err.message
            : String(err);
      const isTransportTimeout = message.includes("request timed out");

      // Session-switch race guard — if the user moved to another session
      // mid-send, this error belongs to the old conversation. The new
      // session's loadMessages already reset the status; only release
      // this run's local bookkeeping.
      if (
        !isTransportTimeout &&
        sessionId &&
        useSessionStore.getState().currentSessionId !== sessionId
      ) {
        pendingAssistantId = null;
        clearStallWatchdog();
        return;
      }

      set((s) => {
        // A client/proxy timeout does not prove the backend run ended.
        // Keep the stream active and let WebSocket status/done events
        // provide the authoritative terminal state.
        if (isTransportTimeout) {
          return s;
        }
        // The WebSocket error event may have already marked the active
        // assistant message as failed before the HTTP error arrives.
        if (
          s.status === "error" &&
          s.messages.some((m) => m.role === "assistant" && m.status === "failed")
        ) {
          return s;
        }
        const pending = pendingAssistantId;
        if (pending && s.messages.some((m) => m.id === pending)) {
          pendingAssistantId = null;
          activeToolCalls.clear();
          activeToolNames.clear();
          return {
            status: "error",
            error: message,
            messages: s.messages.map((m) =>
              m.id === pending
                ? {
                    ...m,
                    text: message,
                    streaming: false,
                    status: "failed",
                    error: message,
                    retry_content: parts ? displayText : text,
                  }
                : m,
            ),
          };
        }
        pendingAssistantId = null;
        activeToolCalls.clear();
        activeToolNames.clear();
        return {
          status: "error",
          error: message,
          messages: trimArray([
            ...s.messages,
            {
              id: `err-${Date.now()}`,
              role: "system",
              text: `Error: ${message}`,
              streaming: false,
              status: "failed",
              error: message,
              retry_content: parts ? displayText : text,
              created_at: Date.now(),
            },
          ], MAX_MESSAGES),
        };
      });
      if (isTransportTimeout) {
        toast.info(strings.toasts.agentBusy, strings.toasts.agentBusyDetail);
      } else {
        toast.error(strings.toasts.sendFailed, message);
      }
    }
  },

  continueRun: async (runId) => {
    const sessionId = useSessionStore.getState().currentSessionId;
    if (!sessionId) return;
    if (get().status === "sending" || get().status === "streaming" || get().status === "cancelling") return;
    ++_viewRevision;
    set({ status: "sending", error: null });
    try {
      const result = await trackRequest(sessionId, typedIPC.continueRun(sessionId, runId));
      if (useSessionStore.getState().currentSessionId !== sessionId) return;
      if (!("message_id" in result)) {
        // Guards: storage unavailable, or the latest run did not end
        // budget-truncated. Surface the reason, keep the log intact.
        toast.error(strings.chat.continueRun.failed, result.error);
        set({ status: "idle" });
        return;
      }
      // Same envelope as send(): chunks normally populated the reply
      // over WebSocket already; only add a fallback bubble when the
      // stream produced nothing (e.g. mock-mode timing edge).
      set((s) => {
        if (s.messages.some((m) => m.id === result.message_id)) {
          return {
            messages: s.messages.map((m) =>
              m.id === result.message_id
                ? { ...m, status: receiptMessageStatus(result), streaming: false,
                    metadata: { ...m.metadata, run_id: result.run_id, status: result.run_status, verification: result.verification } }
                : m,
            ),
            status: "idle",
          };
        }
        return {
          messages: trimArray(ensureMessage(s.messages, {
            id: result.message_id,
            role: "assistant",
            text: result.text || "",
            streaming: false,
            status: receiptMessageStatus(result),
            created_at: Date.now(),
            metadata: { run_id: result.run_id, status: result.run_status, verification: result.verification, ...(result.truncated ? { truncated: true } : {}) },
          }), MAX_MESSAGES),
          status: "idle",
        };
      });
    } catch (err) {
      if (useSessionStore.getState().currentSessionId !== sessionId) return;
      const message = err instanceof IPCError || err instanceof Error
        ? err.message
        : String(err);
      toast.error(strings.chat.continueRun.failed, message);
      set({ status: "idle", error: message });
    }
  },

  cancel: async () => {
    ++_viewRevision;
    clearStallWatchdog();
    const sid = useSessionStore.getState().currentSessionId;
    if (!sid) return;
    const request = inFlightRequests.get(sid);
    const previous = get();
    set((s) => ({
      status: "cancelling",
      messages: s.messages.map((m) =>
        m.role === "assistant" && (m.streaming || m.status === "queued" || m.status === "streaming")
          ? { ...m, status: "cancelling" }
          : m,
      ),
    }));
    try {
      const pinned = useSessionStore.getState().sessions.find((session) => session.id === sid)?.engine;
      if (pinned === "codex") {
        const { runs } = await typedIPC.listRuns({ session_id: sid });
        const active = runs.filter((run) => ["queued", "running", "planning", "awaiting_approval", "waiting_input"].includes(run.status));
        await Promise.all(active.map((run) => typedIPC.cancelRun(run.id)));
        const { useRunTimelineStore } = await import("./runStore");
        await useRunTimelineStore.getState().loadForSession(sid);
        return;
      }
      await typedIPC.cancelAgent(sid);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.cancelFailed, message);
      if (useSessionStore.getState().currentSessionId === sid) {
        set((s) => ({
          status: s.status === "cancelling" ? previous.status : s.status,
          messages: s.messages.map((m) => m.status === "cancelling"
            ? { ...m, status: previous.messages.find((old) => old.id === m.id)?.status
              ?? (m.streaming ? "streaming" : "completed") }
            : m),
        }));
        resetStallWatchdogForCurrentActivity();
      }
      return;
    }
    // The cancellation RPC only acknowledges the request. The original run
    // response determines whether cancellation or committed completion won.
    if (request) await request.catch(() => undefined);
    else if (useSessionStore.getState().currentSessionId === sid) await get().loadMessages(sid);
  },

  retryMessage: async (messageId: string) => {
    const state = get();
    const idx = state.messages.findIndex((m) => m.id === messageId);
    if (idx < 0) return;
    const failed = state.messages[idx];
    const previousUser = [...state.messages.slice(0, idx)]
      .reverse()
      .find((m) => m.role === "user");
    const content = failed.retry_content ?? previousUser?.text ?? "";
    if (!content.trim()) return;
    set((s) => ({
      messages: s.messages.filter((m) => m.id !== messageId),
      status: "idle",
      error: null,
    }));
    await get().send(content);
  },

  updateMessage: async (messageId: string, text: string) => {
    ++_viewRevision;
    localMessageEdits.add(messageId);
    try {
      await typedIPC.updateMessage({ message_id: messageId, content: text });
      ++_viewRevision;
      set((s) => ({
        messages: s.messages.map((m) =>
          m.id === messageId ? { ...m, text, retry_content: text } : m
        ),
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.messageUpdateFailed, message);
    }
  },

  deleteMessage: async (messageId: string) => {
    localMessageEdits.add(messageId);
    ++_viewRevision;
    try {
      await typedIPC.deleteMessage({ message_id: messageId });
      ++_viewRevision;
      set((s) => ({
        messages: s.messages.filter((m) => m.id !== messageId),
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.messageDeleteFailed, message);
    }
  },

  submitAskUser: async (answers) => {
    const current = get().askUser;
    if (!current) return;
    try {
      const result = await typedIPC.answerUser(current.request_id, answers, false,
        current.engine === "codex" && current.run_id ? { session_id: current.session_id, run_id: current.run_id } : undefined);
      if (!result.ok) {
        // The backend explicitly rejected the answer (unknown/expired
        // request_id) — the card is dead weight, drop it.
        if (get().askUser?.request_id === current.request_id) { ++askVersion; closedAskIds.add(current.request_id); setLiveQuestion(null); }
        toast.error(strings.toasts.askUserSubmitFailed, result.error);
        return;
      }
      if (get().askUser?.request_id === current.request_id) { ++askVersion; closedAskIds.add(current.request_id); setLiveQuestion(null); }
    } catch (err) {
      // Transport failure — keep the card so the user can retry while
      // the backend timeout window is still open.
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.askUserSubmitFailed, message);
    }
  },

  skipAskUser: async () => {
    const current = get().askUser;
    if (!current) return;
    if (current.engine === "codex") {
      try {
        const result = await typedIPC.answerUser(current.request_id, [], true,
          current.run_id ? { session_id: current.session_id, run_id: current.run_id } : undefined);
        if (!result.ok) toast.error(strings.toasts.askUserSubmitFailed, result.error);
        if (get().askUser?.request_id === current.request_id) { ++askVersion; closedAskIds.add(current.request_id); setLiveQuestion(null); }
      } catch (err) {
        toast.error(strings.toasts.askUserSubmitFailed, err instanceof Error ? err.message : String(err));
      }
      return;
    }
    await get().submitAskUser(current.questions.map(() => "(skipped by user)"));
  },

  reconcileRuns: async (runs, stillCurrent, includeActive = false) => {
    const latest = runs[0];
    const terminal = new Set(["completed", "failed", "cancelled", "interrupted", "budget_paused"]);
    if (!latest || runs.some((run) => run.session_id !== latest.session_id)) return;
    const sid = latest.session_id;
    if (runs.some((run) => !terminal.has(run.status))) {
      if (!includeActive) return;
      const seq = ++_reconcileSeq, loadSeq = _loadSeq;
      const activeIds = new Set(runs.filter((run) => !terminal.has(run.status)).map((run) => run.id));
      try {
        const result = await typedIPC.listMessages(sid, { limit: 500 });
        if (seq !== _reconcileSeq || loadSeq !== _loadSeq || !stillCurrent()
            || useSessionStore.getState().currentSessionId !== sid) return;
        const restored = (result.messages ?? []).filter((message) => message.role === "tool"
          && !!message.tool_call_id && !!message.metadata?.run_id && activeIds.has(message.metadata.run_id));
        const completedCalls: string[] = [];
        set((state) => {
          const existing = new Set(state.messages.filter((message) => message.role === "tool"
            && !message.id.startsWith("tc-")).map((message) => message.tool_call_id));
          const additions: Message[] = [];
          for (const message of restored) {
            const id = message.tool_call_id!;
            if (existing.has(id) || state.messages.some((row) => row.id === message.id)) continue;
            existing.add(id);
            additions.push({ ...message, id: `tr-${id}`, text: message.text ?? message.content ?? "",
              streaming: false, parent_id: `tc-${id}` });
          }
          // Never evict a local row to make room for recovered evidence.
          const available = Math.max(0, MAX_MESSAGES - state.messages.length);
          if (!available || !additions.length) return state;
          const visible = additions.slice(-available);
          visible.forEach((message) => recoveredMessageIds.add(message.id));
          visible.forEach((message) => completedCalls.push(message.tool_call_id!));
          const index = state.messages.findIndex((message) => message.role === "assistant" && message.streaming);
          const insert = index < 0 ? state.messages.length : index;
          return { messages: [...state.messages.slice(0, insert), ...visible, ...state.messages.slice(insert)] };
        });
        completedCalls.forEach((id) => { activeToolCalls.delete(id); activeToolNames.delete(id); });
        if (completedCalls.length) ++_viewRevision;
        if (completedCalls.length && get().status === "streaming") resetStallWatchdogForCurrentActivity();
      } catch {
        // Preserve live content; the next reconnect or explicit refresh retries.
      }
      return;
    }
    const seq = ++_reconcileSeq;
    const loadSeq = _loadSeq;
    const revision = _viewRevision;
    reconcilingRunIds = new Set(runs.map((run) => run.id));
    const isCurrent = () => seq === _reconcileSeq && loadSeq === _loadSeq && stillCurrent()
      && useSessionStore.getState().currentSessionId === sid;
    try {
      // Let the original reply handler finish before replacing provisional rows.
      await inFlightRequests.get(sid)?.catch(() => undefined);
      await Promise.resolve();
      if (!isCurrent() || revision !== _viewRevision || inFlightRequests.has(sid)) return;
      const acceptedKeys = new Set(runs.filter((run) => run.user_message_id).map((run) => run.request_key));
      if (get().status === "sending") {
        const pendingKey = get().messages.find((m) => m.id === pendingAssistantId)?.metadata?.request_key;
        if (!pendingKey || !acceptedKeys.has(pendingKey)) return;
      }
      const result = await typedIPC.listMessages(sid, { limit: 500 });
      if (!isCurrent() || revision !== _viewRevision || inFlightRequests.has(sid)) return;
      const prior = new Map(get().messages.map((message) => [message.id, message]));
      for (const run of runs) {
        if (!run.user_message_id || !run.request_key) continue;
        const local = get().messages.find((message) => message.role === "user"
          && message.metadata?.request_key === run.request_key);
        if (local) prior.set(run.user_message_id, local);
      }
      const saved: Message[] = (result.messages ?? []).filter((m) =>
        !localMessageEdits.has(m.id) || prior.has(m.id)).map((m) => {
        const local = prior.get(m.id);
        if (local && localMessageEdits.has(m.id)) return local;
        return {
          ...m, text: m.text ?? m.content ?? "", streaming: false, status: m.status ?? "completed",
          // Keep the measured virtual row and component state when a provisional
          // user ID is replaced; remounting a tall row briefly collapses the scroll.
          ...(local ? { render_key: local.render_key ?? local.id } : {}),
        };
      });
      const savedIds = new Set(saved.map((m) => m.id));
      const byRun = new Map(runs.map((run) => [run.id, run]));
      const retained = get().messages.filter((message) => {
        if (savedIds.has(message.id)) return false;
        const key = message.metadata?.request_key;
        if (key && !acceptedKeys.has(key)) return true; // retain an unconfirmed local submission
        const run = byRun.get(message.metadata?.run_id ?? "");
        return message.role === "assistant" && !!run && run.status !== "completed";
      }).map((message): Message => {
        const run = byRun.get(message.metadata?.run_id ?? "");
        return run ? { ...message, streaming: false, status: run.status === "cancelled" ? "cancelled" : "failed" } : message;
      });
      const hasUnconfirmedInput = retained.some((m) => m.metadata?.request_key && !acceptedKeys.has(m.metadata.request_key));
      recoveredMessageIds.clear();
      recoveredRunIds.clear();
      savedIds.forEach((id) => recoveredMessageIds.add(id));
      runs.forEach((run) => recoveredRunIds.add(run.id));
      if (!hasUnconfirmedInput) {
        pendingAssistantId = null;
        activeToolCalls.clear();
        activeToolNames.clear();
        clearStallWatchdog();
      }
      ++_viewRevision;
      set({ messages: trimArray([...saved, ...retained], MAX_MESSAGES),
        ...(!hasUnconfirmedInput ? { status: latest.status === "failed" || latest.status === "interrupted" ? "error" as const : "idle" as const,
          error: latest.error ?? null, askUser: null } : {}),
      });
    } catch {
      // A missing history response must never erase the current view. A fresh
      // reconnect or explicit timeline refresh will retry the reconciliation.
    } finally {
      if (seq === _reconcileSeq) reconcilingRunIds.clear();
    }
  },

  loadMessages: async (sessionId: string) => {
    if (!sessionId) return;
    // Bump the sequence counter so any in-flight load from a
    // previous session is silently discarded.
    const seq = ++_loadSeq;
    const revision = _viewRevision;
    const initiallyEmpty = get().messages.length === 0;
    set({ loadingMessages: true });
    try {
      const result = await typedIPC.listMessages(sessionId);
      // Stale response — the user has already switched away.
      if (seq !== _loadSeq) return;
      if (useSessionStore.getState().currentSessionId !== sessionId) return;
      // Convert backend rows (content → text, add streaming: false)
      const msgs: Message[] = (result.messages ?? []).map((m) => ({
        id: m.id,
        role: m.role,
        text: m.text ?? m.content ?? "",
        streaming: m.streaming ?? m.status === "streaming",
        status: m.status ?? "completed",
        created_at: m.created_at,
        metadata: m.metadata,
        tool_call_id: m.tool_call_id,
        tool_name: m.tool_name,
        tool_args: m.tool_args,
      }));
      const state = get();
      const merged = mergeHistory(msgs, state.messages, initiallyEmpty);
      const active = state.status === "sending" || state.status === "streaming" || state.status === "cancelling";
      set({ messages: merged.messages, loadingMessages: false,
        ...(!active && revision === _viewRevision ? { status: "idle" as const, error: null } : {}) });
      if (merged.incomplete) toast.info("部分历史暂未合并", "当前内容已保留，请在任务结束后刷新历史。" );
    } catch (err) {
      // Stale — skip error toast for abandoned requests.
      if (seq !== _loadSeq) return;
      // An unavailable history endpoint is not an empty conversation. Session
      // switches already reset their view; a refresh failure must preserve the
      // current transcript and execution/cancellation state.
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.messagesLoadFailed, message);
      set({ loadingMessages: false });
    } finally {
      if (seq === _loadSeq && pendingActiveSession === sessionId
          && useSessionStore.getState().currentSessionId === sessionId) {
        pendingActiveSession = null;
        if (get().status === "idle") set({ status: "streaming" });
      }
      if (seq === _loadSeq && deferredStreamSession === sessionId
          && useSessionStore.getState().currentSessionId === sessionId) {
        deferredStreamSession = null;
        requestStreamSnapshot(sessionId);
      }
    }
  },

  reset: () => {
    pendingActiveSession = null;
    deferredStreamSession = null;
    ++askVersion;
    closedAskIds.clear();
    if (askExpiryTimer) clearTimeout(askExpiryTimer);
    askExpiryTimer = null;
    ++_loadSeq;
    ++_viewRevision;
    ++_reconcileSeq;
    recoveredMessageIds.clear();
    recoveredRunIds.clear();
    reconcilingRunIds.clear();
    streamCursors.clear(); localMessageEdits.clear(); streamWarnings.clear(); unavailableStreams.clear();
    clearStallWatchdog();
    pendingAssistantId = null;
    set({ messages: [], status: "idle", error: null, loadingMessages: false, askUser: null });
  },
}));

export type { Message };

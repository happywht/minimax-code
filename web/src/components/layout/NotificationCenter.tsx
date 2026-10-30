/**
 * NotificationCenter — dropdown panel listing recent notifications.
 *
 * Rendered inside ``NotificationBell`` as a floating panel.
 *
 * v0.7.0 — Mobile Connectivity Enhancement
 */
import { useEffect, useRef, useState } from "react";
import {
  Check,
  Info,
  AlertTriangle,
  XCircle,
  Webhook,
  Shield,
  Workflow,
  X,
  Trash2,
} from "lucide-react";
import { Panel } from "../../ui/Panel";
import { Button } from "../../ui/Button";
import { IconButton } from "../../ui/IconButton";
import { EmptyState } from "../../ui/EmptyState";
import { strings } from "../../ui/strings";
import { SkeletonTable } from "./Skeleton";
import { useNotificationStore } from "../../stores/notificationStore";
import type { NotificationEntry } from "../../stores/notificationStore";
import { formatRelative } from "../../lib/time";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores/sessionStore";
import { useChat } from "../../stores/chat";

const TYPE_ICON: Record<string, typeof Info> = {
  info: Info,
  warning: AlertTriangle,
  error: XCircle,
  webhook: Webhook,
  permission: Shield,
  workflow: Workflow,
};

function typeIcon(type: string) {
  return TYPE_ICON[type] ?? Info;
}

export function NotificationCenter(): JSX.Element {
  const entries = useNotificationStore((s) => s.entries);
  const unreadCount = useNotificationStore((s) => s.unreadCount);
  const loading = useNotificationStore((s) => s.loading);
  const markRead = useNotificationStore((s) => s.markRead);
  const markAllRead = useNotificationStore((s) => s.markAllRead);
  const deleteNotification = useNotificationStore((s) => s.deleteNotification);
  const setOpen = useNotificationStore((s) => s.setOpen);
  const refresh = useNotificationStore((s) => s.refresh);
  const purge = useNotificationStore((s) => s.purge);

  useEffect(() => {
    refresh({ limit: 50 });
  }, [refresh]);

  const hasReadEntries = entries.some((e) => e.read);

  const handlePurgeRead = async () => {
    const accepted = await requestConfirmation({
      title: strings.layout.notifications.purgeReadTitle,
      description: strings.layout.notifications.purgeReadDesc,
      confirmLabel: strings.layout.notifications.purgeReadLabel,
    });
    if (!accepted) return;
    // Purge everything already read ("before now", read-only); the store
    // refreshes the list itself and toasts on failure.
    await purge(new Date().toISOString(), true);
  };

  return (
    <Panel
      data-testid="notification-center"
      title={
        <span className="normal-case">
          {strings.layout.notifications.title}
          {unreadCount > 0 && (
            <span className="ml-1.5 text-ink-1">
              {strings.layout.notifications.unreadSuffix(unreadCount)}
            </span>
          )}
        </span>
      }
      actions={
        <>
          {unreadCount > 0 && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              icon={<Check size={12} />}
              onClick={() => markAllRead()}
              title={strings.layout.notifications.markAllReadTitle}
            >
              {strings.layout.notifications.readAll}
            </Button>
          )}
          {hasReadEntries && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              icon={<Trash2 size={12} />}
              onClick={() => void handlePurgeRead()}
              data-testid="notification-purge-read"
            >
              {strings.layout.notifications.purgeRead}
            </Button>
          )}
          <IconButton
            aria-label={strings.layout.notifications.close}
            size="sm"
            onClick={() => setOpen(false)}
          >
            <X size={12} />
          </IconButton>
        </>
      }
      className="w-80 shadow-pop"
      flush
    >
      <div className="max-h-80 overflow-y-auto">
        {loading && entries.length === 0 && (
          <div className="p-3">
            <SkeletonTable rows={3} />
          </div>
        )}
        {!loading && entries.length === 0 && (
          <EmptyState
            title="暂无通知"
            icon={<Info size={24} />}
            testId="notification-center-empty"
          />
        )}
        {entries.map((entry) => (
          <NotificationItem
            key={entry.id}
            entry={entry}
            onMarkRead={markRead}
            onDelete={deleteNotification}
          />
        ))}
      </div>
    </Panel>
  );
}

function NotificationItem({
  entry,
  onMarkRead,
  onDelete,
}: {
  entry: NotificationEntry;
  onMarkRead: (id: string) => void;
  onDelete: (id: string) => void;
}): JSX.Element {
  const Icon = typeIcon(entry.type);
  const [attentionError, setAttentionError] = useState("");
  const [attentionBusy, setAttentionBusy] = useState(false);
  const attentionEpoch = useRef(0);
  useEffect(() => {
    const unsubscribe = ipc.onSideCar(() => { attentionEpoch.current++; setAttentionBusy(false); });
    return () => { attentionEpoch.current++; unsubscribe(); };
  }, []);
  const openAttention = async () => {
    const ticket = ++attentionEpoch.current, host = ipc.agentBaseUrl;
    const { currentSessionId, currentProjectId } = useSessionStore.getState();
    let expectedSession = currentSessionId, expectedProject = currentProjectId;
    const current = () => ticket === attentionEpoch.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentSessionId === expectedSession && useSessionStore.getState().currentProjectId === expectedProject;
    setAttentionBusy(true); setAttentionError("");
    try {
      const binding = JSON.parse(entry.source_id || "") as { job_id: string; occurrence_id: string; run_id: string;
        session_id: string; project_id: string; request_id: string; kind: string };
      if (!binding.job_id || !binding.occurrence_id || !binding.run_id || !binding.session_id || !binding.project_id ||
          !binding.request_id || !["permission", "question"].includes(binding.kind)) throw new Error("通知关联不完整");
      const [history, snapshot] = await Promise.all([
        typedIPC.listScheduleOccurrences(binding.job_id), typedIPC.getRunSteps(binding.run_id),
      ]);
      if (!current()) return;
      const occurrence = history.occurrences.find((row) => row.id === binding.occurrence_id);
      const schedule = snapshot.run.metadata?.schedule as { occurrence_id?: string; job_id?: string; revision?: number } | undefined;
      if (!occurrence || occurrence.job_id !== binding.job_id || occurrence.run_id !== binding.run_id ||
          occurrence.session_id !== binding.session_id || occurrence.context?.project_id !== binding.project_id ||
          occurrence.context?.host !== "local" || occurrence.context?.engine !== "native" ||
          snapshot.run.id !== binding.run_id || snapshot.run.session_id !== binding.session_id ||
          snapshot.run.metadata?.project_id !== binding.project_id || snapshot.run.metadata?.workspace_snapshot !== occurrence.context.workspace ||
          schedule?.occurrence_id !== occurrence.id || schedule.job_id !== binding.job_id || schedule.revision !== occurrence.revision)
        throw new Error("运行与定时任务的关联已变化，请查看历史");
      const pending = binding.kind === "permission"
        ? snapshot.pending_native_permissions?.some((item) => item.request_id === binding.request_id && item.run_id === binding.run_id && item.session_id === binding.session_id)
        : (snapshot.pending_ask_users ?? (snapshot.pending_ask_user ? [snapshot.pending_ask_user] : []))
          .some((item) => item.request_id === binding.request_id && item.run_id === binding.run_id && item.session_id === binding.session_id);
      if (!pending) throw new Error("请求已解决、超时或失效，请查看运行历史");
      if (!useSessionStore.getState().setCurrentProject(binding.project_id)) return;
      expectedProject = binding.project_id;
      if (!useSessionStore.getState().setCurrent(binding.session_id)) return;
      expectedSession = binding.session_id;
      if (binding.kind === "question") {
        // Re-read after switching sessions: events received in the old session
        // cannot guard a snapshot taken before the switch.
        const version = useChat.getState().askRecoveryVersion();
        const latest = await typedIPC.getRunSteps(binding.run_id);
        if (!current()) return;
        if (latest.run.id !== binding.run_id || latest.run.session_id !== binding.session_id) throw new Error("运行关联已变化");
        const question = (latest.pending_ask_users ?? (latest.pending_ask_user ? [latest.pending_ask_user] : []))
          .find((item) => item.request_id === binding.request_id && item.run_id === binding.run_id && item.session_id === binding.session_id);
        if (!question) throw new Error("请求已解决、超时或失效，请查看运行历史");
        useChat.getState().reconcileAskUser(question, version, binding.session_id);
        if (useChat.getState().askUser?.request_id !== binding.request_id) throw new Error("请求状态已变化，请重新打开通知");
      }
      void useSessionStore.getState().refresh({ loadCurrent: false });
      window.dispatchEvent(new Event("minimax:open-chat"));
      useNotificationStore.getState().setOpen(false);
      if (!entry.read) onMarkRead(entry.id);
    } catch (error) { if (current()) setAttentionError(error instanceof Error ? error.message : "无法读取请求状态"); }
    finally { if (current()) setAttentionBusy(false); }
  };
  const openAttentionHistory = () => {
    try {
      const binding = JSON.parse(entry.source_id || "") as { job_id?: unknown; occurrence_id?: unknown };
      if (typeof binding.job_id !== "string" || !binding.job_id ||
          typeof binding.occurrence_id !== "string" || !binding.occurrence_id) {
        throw new Error("通知关联不完整，无法定位历史记录");
      }
      window.dispatchEvent(new CustomEvent("minimax:open-scheduled", {
        detail: { jobId: binding.job_id, occurrenceId: binding.occurrence_id },
      }));
      useNotificationStore.getState().setOpen(false);
      if (!entry.read) onMarkRead(entry.id);
    } catch (error) {
      setAttentionError(error instanceof Error ? error.message : "无法定位历史记录");
    }
  };
  return (
    <div
      className={`group flex items-start gap-2 border-b border-line/50 px-3 py-2 text-xs transition-colors hover:bg-surface-3 ${
        entry.read ? "opacity-60" : ""
      }`}
      data-testid={`notification-item-${entry.id}`}
    >
      <Icon size={14} className="mt-0.5 shrink-0 text-ink-2" />
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-1">
          <span className="font-medium leading-tight text-ink-0">
            {entry.title}
          </span>
          <span className="shrink-0 text-[11px] text-ink-1">
            {formatRelative(entry.created_at)}
          </span>
        </div>
        {entry.body && (
          <p className="mt-0.5 leading-snug line-clamp-2 text-ink-1">
            {entry.body}
          </p>
        )}
        {entry.source === "schedule_attention" && (
          <div className="mt-1">
            <Button type="button" variant="ghost" size="sm" disabled={attentionBusy || entry.title === "定时任务请求已结束"}
              className="h-auto px-0 py-0 text-[11px] hover:underline" onClick={() => void openAttention()}>
              {attentionBusy ? "读取当前请求…" : entry.title === "定时任务请求已结束" ? "请求已结束" : "查看并处理请求"}
            </Button>
            <Button type="button" variant="ghost" size="sm"
              className="ml-2 h-auto px-0 py-0 text-[11px] hover:underline" onClick={openAttentionHistory}>
              查看关联历史
            </Button>
            {attentionError && <p role="status" className="text-status-warning">{attentionError}</p>}
          </div>
        )}
        {entry.source === "schedule" && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="mt-1 h-auto px-0 py-0 text-[11px] hover:underline"
            onClick={() => {
              let target: { jobId: string; occurrenceId?: string } | undefined;
              try {
                const binding = JSON.parse(entry.source_id || "") as { job_id?: string; occurrence_id?: string };
                if (binding.job_id) target = { jobId: binding.job_id, occurrenceId: binding.occurrence_id };
              } catch {
                if (entry.source_id) target = { jobId: entry.source_id };
              }
              window.dispatchEvent(new CustomEvent("minimax:open-scheduled", { detail: target }));
              useNotificationStore.getState().setOpen(false);
              if (!entry.read) onMarkRead(entry.id);
            }}
          >
            查看定时任务历史
          </Button>
        )}
        <div className="mt-1 flex gap-2 opacity-0 transition-opacity group-hover:opacity-100">
          {!entry.read && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => onMarkRead(entry.id)}
              className="h-auto px-0 py-0 text-[11px] hover:underline"
            >
              {strings.layout.notifications.markRead}
            </Button>
          )}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onDelete(entry.id)}
            className="h-auto px-0 py-0 text-[11px] text-status-error hover:bg-transparent hover:text-status-error hover:underline"
          >
            {strings.layout.notifications.delete}
          </Button>
        </div>
      </div>
      {!entry.read && (
        <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-accent" />
      )}
    </div>
  );
}

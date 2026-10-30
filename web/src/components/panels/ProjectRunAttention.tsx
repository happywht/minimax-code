import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useChat, usePermissionStore, useSessionStore } from "../../stores";
import type { AskUserData, PermissionRequestData, RunStepsResult } from "../../types/ipc";
import { Button } from "../../ui";
import { toast } from "../layout/ErrorBoundary";

type Kind = "native" | "codex" | "question";
const live = (item: { remaining_ms?: number }) => item.remaining_ms === undefined || item.remaining_ms > 0;

export function ProjectRunAttention({ snapshot, host, service, project, onOpenSession }: {
  snapshot: RunStepsResult; host: string; service: string; project: string; onOpenSession: () => void;
}): JSX.Element | null {
  const originSession = useSessionStore((state) => state.currentSessionId);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const epoch = useRef(0), lock = useRef(false);
  const run = snapshot.run.id, session = snapshot.run.session_id;
  useEffect(() => { ++epoch.current; setBusy(false); setError(""); return () => { ++epoch.current; }; }, [host, service, project, session, run, originSession]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; setBusy(false); setError("连接变化，请刷新运行记录。"); }), []);
  const native = (snapshot.pending_native_permissions ?? []).filter((p) => p.run_id === run && p.session_id === session && !p.tool.startsWith("codex:") && live(p));
  const codex = (snapshot.pending_codex_permissions ?? []).filter((p) => p.run_id === run && p.session_id === session && p.tool.startsWith("codex:") && live(p));
  const questions = (snapshot.pending_ask_users ?? (snapshot.pending_ask_user ? [snapshot.pending_ask_user] : []))
    .filter((q) => q.run_id === run && q.session_id === session && live(q));
  const waiting = ["awaiting_approval", "waiting_input"].includes(snapshot.run.status);
  if (!waiting && !native.length && !codex.length && !questions.length) return null;
  const matches = (value: RunStepsResult) => value.run.id === run && value.run.session_id === session &&
    value.run.metadata?.project_id === project && Number.isSafeInteger(value.cursor) && value.cursor >= 0 &&
    value.steps.every((step) => step.run_id === run && step.session_id === session);
  const find = (value: RunStepsResult, kind: Kind, id: string): PermissionRequestData | AskUserData | undefined => {
    const values = kind === "native" ? value.pending_native_permissions : kind === "codex" ? value.pending_codex_permissions :
      value.pending_ask_users ?? (value.pending_ask_user ? [value.pending_ask_user] : []);
    return values?.find((item) => item.request_id === id && item.run_id === run && item.session_id === session && live(item) &&
      (kind === "question" || ("tool" in item && item.tool.startsWith("codex:") === (kind === "codex"))));
  };
  const open = async (kind: Kind, id: string) => {
    if (lock.current) return;
    const ticket = ++epoch.current;
    const current = () => epoch.current === ticket && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentProjectId === project && useSessionStore.getState().currentSessionId === originSession;
    lock.current = true; setBusy(true); setError("");
    let navigated = false, invalidated = false;
    let offConnection: (() => void) | undefined, offScope: (() => void) | undefined;
    try {
      const [identity, fresh, owner] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getRunSteps(run), typedIPC.getSession(session)]);
      const after = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (identity.service_id !== service || after.service_id !== service || !service || !matches(fresh) || !find(fresh, kind, id) ||
          owner.session.id !== session || owner.session.project_id !== project || owner.session.archived) throw new Error("服务、运行、会话或请求已变化，请刷新后重试。");
      const existing = useChat.getState().askUser;
      if (existing && existing.questions.some((q) => q.isSecret) &&
          (existing.session_id !== session || kind === "question" && existing.request_id !== id))
        throw new Error("当前有未完成的保密问题；请先处理后再切换，避免丢失未保存输入。");
      if (originSession !== session && !useSessionStore.getState().setCurrent(session)) return;
      onOpenSession(); navigated = true;
      const permissionVersion = usePermissionStore.getState().recoveryVersion(session);
      const askVersion = useChat.getState().askRecoveryVersion();
      const selected = () => !invalidated && ipc.agentBaseUrl === host && useSessionStore.getState().currentProjectId === project &&
        useSessionStore.getState().currentSessionId === session;
      offConnection = ipc.onSideCar(() => { invalidated = true; });
      offScope = useSessionStore.subscribe((state) => { if (state.currentProjectId !== project || state.currentSessionId !== session || ipc.agentBaseUrl !== host) invalidated = true; });
      const [check, latest, ownerAfter] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getRunSteps(run), typedIPC.getSession(session)]);
      const end = await typedIPC.getServiceIdentity();
      if (!selected()) return;
      const request = find(latest, kind, id);
      if (check.service_id !== service || end.service_id !== service || !matches(latest) || !request ||
          ownerAfter.session.id !== session || ownerAfter.session.project_id !== project || ownerAfter.session.archived)
        throw new Error("请求已结束或服务、运行、会话归属已变化，请查看会话记录。");
      if (kind === "question") {
        const question = request as AskUserData;
        const currentQuestion = useChat.getState().askUser;
        if (currentQuestion && currentQuestion.request_id !== id && currentQuestion.questions.some((q) => q.isSecret))
          throw new Error("当前有未完成的保密问题；请先处理后再打开其他问题。");
        useChat.getState().reconcileAskUser(question, askVersion, session);
        if (useChat.getState().askUser?.request_id !== id) throw new Error("问题状态已变化，请刷新会话。");
        requestAnimationFrame(() => {
          if (selected()) window.dispatchEvent(new CustomEvent("minimax:open-run-question", { detail: { host, session, run, request: id } }));
        });
      } else {
        const list = kind === "native" ? latest.pending_native_permissions : latest.pending_codex_permissions;
        if (!list || list.some((item) => item.run_id !== run || item.session_id !== session || item.tool.startsWith("codex:") !== (kind === "codex")))
          throw new Error("授权请求快照不完整，请刷新会话。");
        usePermissionStore.getState().reconcilePermissions(session, [run], kind === "native" ? list : null, kind === "codex" ? list : null, permissionVersion);
        const permissions = usePermissionStore.getState(), item = permissions.pending[id];
        if (!item || item.run_id !== run || item.session_id !== session || permissions.resolving[id]) throw new Error("授权请求已变化，请刷新会话。");
        window.dispatchEvent(new CustomEvent("minimax:open-run-permission", { detail: { host, session, run, request: id } }));
      }
      void useSessionStore.getState().refresh({ loadCurrent: false });
    } catch (reason) {
      const text = reason instanceof Error ? reason.message : String(reason);
      if (navigated) { if (!invalidated && ipc.agentBaseUrl === host && useSessionStore.getState().currentSessionId === session && useSessionStore.getState().currentProjectId === project) toast.error("无法打开任务请求", text); }
      else if (current()) setError(text);
    } finally { offConnection?.(); offScope?.(); lock.current = false; if (current()) setBusy(false); }
  };
  return <section className="mt-2 space-y-2 rounded border border-status-warning/30 p-2 text-xs" aria-label="任务待处理请求">
    <h4 className="font-medium">待处理请求</h4>
    <p>以下为读取时的请求快照；打开时会再次确认，过期或已处理的请求不能继续操作。</p>
    {!native.length && !codex.length && !questions.length && <p>当前没有可确认的请求；请刷新运行或打开所属会话核对。</p>}
    {[...native.map((request) => ({ request, kind: "native" as const })), ...codex.map((request) => ({ request, kind: "codex" as const }))].map(({ request, kind }) =>
      <div key={`${kind}:${request.request_id}`} className="flex flex-wrap items-center gap-2"><span className="break-all">待授权：{request.tool} · {request.request_id}</span>
        <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void open(kind, request.request_id)}>查看此授权请求</Button></div>)}
    {questions.map((question) => <div key={question.request_id} className="flex flex-wrap items-center gap-2"><span className="break-words">待回答：{question.questions[0]?.header || question.questions[0]?.question || "Agent 提问"} · {question.request_id}</span>
      <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void open("question", question.request_id)}>回答此问题</Button></div>)}
    {error && <p role="alert" className="break-words text-status-warning">{error}</p>}
    <p className="text-ink-2">查看不会授权、回答、恢复运行或放宽预算；请在原会话核对后自行处理。</p>
  </section>;
}

import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useChat, useSessionStore } from "../../stores";
import type { AgentRun, RunInputText } from "../../types/ipc";
import { Button } from "../../ui";

type Prepared = { source: RunInputText; thread: string; host: string };

/** User-selected reuse of an existing input; never sends or replaces the draft. */
export function CodexInputDraft({ run }: { run: AgentRun }): JSX.Element | null {
  const session = useSessionStore((s) => s.currentSessionId);
  const project = useSessionStore((s) => s.currentProjectId);
  const [prepared, setPrepared] = useState<Prepared | null>(null), [excerpt, setExcerpt] = useState("");
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const epoch = useRef(0), working = useRef(false);
  useEffect(() => {
    const clear = () => { epoch.current++; setPrepared(null); setExcerpt(""); setBusy(false); setNotice(""); };
    clear();
    const stop = ipc.onSideCar(clear);
    return () => { epoch.current++; stop(); };
  }, [run.id, run.status, session, project]);
  if (run.metadata?.engine !== "codex" || run.status !== "completed" || !session || session !== run.session_id) return null;
  const sourceSession = session;
  const sourceProject = run.metadata?.project_id ?? null;
  if (sourceProject !== project || (sourceProject !== null && typeof sourceProject !== "string")) return null;
  const boundProject = sourceProject as string | null;

  const current = (ticket: number, host: string) => {
    const state = useSessionStore.getState(), record = state.sessions.find((item) => item.id === sourceSession);
    return ticket === epoch.current && ipc.agentBaseUrl === host && state.currentSessionId === sourceSession &&
      state.currentProjectId === boundProject && (record?.project_id ?? null) === boundProject && record?.engine === "codex";
  };
  const read = async (ticket: number, host: string): Promise<Prepared> => {
    const identity = await typedIPC.getServiceIdentity();
    if (!current(ticket, host) || !identity.service_id) throw new Error("服务或会话已变化。");
    const [snapshot, saved] = await Promise.all([typedIPC.getRunSteps(run.id), typedIPC.getSession(sourceSession)]);
    const thread = saved.session.engine_thread_id;
    if (!current(ticket, host) || !identity.service_id || saved.session.id !== sourceSession ||
        saved.session.project_id !== boundProject || saved.session.engine !== "codex" || saved.session.archived ||
        typeof thread !== "string" || !thread || snapshot.run.id !== run.id || snapshot.run.session_id !== sourceSession ||
        snapshot.run.status !== "completed" || snapshot.run.metadata?.engine !== "codex" ||
        (snapshot.run.metadata?.project_id ?? null) !== boundProject || snapshot.run.metadata?.codex_thread_id !== thread ||
        !Number.isSafeInteger(snapshot.cursor) || snapshot.cursor < 0) throw new Error("会话、线程或运行记录已变化，无法准备草稿。");
    const source = await typedIPC.getRunInputText({ run_id: run.id, session_id: sourceSession, project_id: boundProject,
      expected_service_id: identity.service_id, expected_cursor: snapshot.cursor });
    const [after, sessionAfter] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getSession(sourceSession)]);
    if (!current(ticket, host) || after.service_id !== identity.service_id || sessionAfter.session.id !== sourceSession ||
        sessionAfter.session.project_id !== boundProject || sessionAfter.session.engine !== "codex" || sessionAfter.session.archived ||
        sessionAfter.session.engine_thread_id !== thread || source.run_id !== run.id || source.session_id !== sourceSession ||
        source.project_id !== boundProject || source.service_id !== identity.service_id || source.cursor !== snapshot.cursor ||
        typeof source.text !== "string" || source.text.length > 16000 || typeof source.truncated !== "boolean" ||
        !Number.isInteger(source.image_count) || source.image_count < 0 || source.image_count > 5) throw new Error("输入或线程归属已变化，请重新准备。");
    return { source, thread, host };
  };
  const prepare = async () => {
    if (working.current) return;
    const ticket = ++epoch.current, host = ipc.agentBaseUrl;
    let changed = false;
    const off = useSessionStore.subscribe((state) => {
      const record = state.sessions.find((item) => item.id === sourceSession);
      if (state.currentSessionId !== sourceSession || state.currentProjectId !== boundProject ||
          (record?.project_id ?? null) !== boundProject || record?.engine !== "codex") changed = true;
    });
    const stop = ipc.onSideCar(() => { changed = true; });
    working.current = true; setBusy(true); setPrepared(null); setExcerpt(""); setNotice("");
    try {
      const result = await read(ticket, host);
      if (changed || !current(ticket, host)) return;
      setPrepared(result); setExcerpt(result.source.text.slice(0, 4000));
    } catch (error) { if (current(ticket, host)) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { off(); stop(); working.current = false; if (ticket === epoch.current) setBusy(false); }
  };
  const append = async () => {
    if (!prepared || working.current || !excerpt.trim()) return;
    const selected = excerpt, ticket = ++epoch.current, host = prepared.host;
    let changed = false;
    const off = useSessionStore.subscribe((state) => {
      const record = state.sessions.find((item) => item.id === sourceSession);
      if (state.currentSessionId !== sourceSession || state.currentProjectId !== boundProject ||
          (record?.project_id ?? null) !== boundProject || record?.engine !== "codex") changed = true;
    });
    const stop = ipc.onSideCar(() => { changed = true; });
    working.current = true; setBusy(true); setNotice("");
    try {
      const fresh = await read(ticket, host);
      if (changed || !current(ticket, host)) return;
      if (fresh.thread !== prepared.thread || JSON.stringify(fresh.source) !== JSON.stringify(prepared.source))
        throw new Error("原任务输入或记录已变化，请重新读取并审阅。");
      if (["sending", "streaming", "cancelling"].includes(useChat.getState().status))
        throw new Error("当前会话正在运行，请结束后再加入草稿。");
      const detail: { host: string; session: string; project: string | null; text: string; result?: string } = {
        host, session: sourceSession, project: boundProject, text: selected,
      };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      setNotice(detail.result ?? "当前聊天输入区未就绪，未加入草稿。");
      if (detail.result === "已添加到当前会话草稿，尚未发送。") { setPrepared(null); setExcerpt(""); }
    } catch (error) { if (current(ticket, host)) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { off(); stop(); working.current = false; if (ticket === epoch.current) setBusy(false); }
  };
  return <div className="space-y-1 text-xs text-ink-2">
    <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void prepare()}>复用原任务文字</Button>
    {notice && <p role="status" className="break-words">{notice}</p>}
    {prepared && <div className="space-y-1 rounded border border-line p-2">
      <p>编辑要追加到当前 Codex 会话草稿的文字；可能包含敏感信息，加入后仍需手动发送。</p>
      <p>原任务有 {prepared.source.image_count} 张图片；图片不会自动加入，可在输入图片处另行选择。</p>
      {(prepared.source.truncated || prepared.source.text.length > 4000) && <p>仅显示前 4000 字，请补充遗漏内容。</p>}
      <textarea aria-label="复用的原任务文字" className="block w-full rounded border border-line bg-surface-1 p-2" rows={5}
        maxLength={4000} disabled={busy} value={excerpt} onChange={(event) => setExcerpt(event.target.value)} />
      <Button type="button" size="sm" variant="secondary" disabled={busy || !excerpt.trim()} onClick={() => void append()}>追加到当前草稿</Button>
    </div>}
  </div>;
}

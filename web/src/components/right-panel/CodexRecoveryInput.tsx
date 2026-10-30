import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { RunInputText } from "../../types/ipc";
import { Button } from "../../ui";

export function CodexRecoveryInput({ run, session, project, service, cursor, disabled, onAdd, engine = "codex" }: {
  run: string; session: string; project: string | null; service: string; cursor: number;
  disabled: boolean; onAdd: (text: string) => string;
  engine?: "native" | "codex";
}): JSX.Element {
  const [value, setValue] = useState<RunInputText | null>(null), [excerpt, setExcerpt] = useState("");
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const epoch = useRef(0);
  const loadedHost = useRef("");
  useEffect(() => {
    const clear = () => { epoch.current++; setValue(null); setExcerpt(""); setBusy(false); setNotice(""); };
    clear();
    const stop = ipc.onSideCar(clear);
    return () => { epoch.current++; stop(); };
  }, [run, session, project, service, cursor, engine]);

  const load = async () => {
    if (busy || disabled) return;
    const ticket = ++epoch.current, host = ipc.agentBaseUrl;
    let changed = false;
    const off = useSessionStore.subscribe((state) => {
      const bound = state.sessions.find((item) => item.id === session)?.project_id ?? null;
      if (state.currentSessionId !== session || (state.currentProjectId ?? "inbox") !== (project ?? "inbox") || bound !== project) changed = true;
    });
    const stop = ipc.onSideCar(() => { changed = true; });
    const current = () => {
      const state = useSessionStore.getState();
      return !changed && ticket === epoch.current && ipc.agentBaseUrl === host && state.currentSessionId === session &&
        (state.currentProjectId ?? "inbox") === (project ?? "inbox") &&
        (state.sessions.find((item) => item.id === session)?.project_id ?? null) === project;
    };
    setBusy(true); setNotice(""); setValue(null); setExcerpt("");
    try {
      const result = await typedIPC.getRunInputText({ run_id: run, session_id: session, project_id: project,
        expected_service_id: service, expected_cursor: cursor });
      if (!current()) return;
      if (result.run_id !== run || result.session_id !== session || result.project_id !== project || result.service_id !== service ||
          result.cursor !== cursor || typeof result.text !== "string" || result.text.length > 16000 ||
          typeof result.truncated !== "boolean" || !Number.isInteger(result.image_count) || result.image_count < 0 || result.image_count > 5)
        throw new Error("原任务输入或归属不一致");
      loadedHost.current = host; setValue(result); setExcerpt(result.text.slice(0, 4000));
    } catch (error) { if (current()) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { off(); stop(); if (ticket === epoch.current) setBusy(false); }
  };
  return <div className="space-y-1 rounded border border-line p-2">
    <Button type="button" size="sm" variant="secondary" disabled={busy || disabled} onClick={() => void load()}>读取原任务文字</Button>
    <p>仅供审阅，可能包含敏感信息；不会自动加入交接或发送。</p>
    {notice && <p role="status" className="break-words text-status-warning">{notice}</p>}
    {value && <>
      {engine === "codex" && <p>原任务包含 {value.image_count} 张图片；此处仅处理文字，图片不会随恢复自动发送。</p>}
      {engine === "native" && <p>恢复将按原流程使用保存的任务输入；此处只为交接添加用户选择的文字。</p>}
      {(value.truncated || value.text.length > 4000) && <p>原文字较长，以下仅显示前 4000 字；请手动补充遗漏内容。</p>}
      <label className="block">编辑需要交接的原任务摘录
        <textarea aria-label="原任务文字摘录" className="mt-1 block w-full rounded border border-line bg-surface-1 p-2" rows={5}
          value={excerpt} maxLength={4000} disabled={disabled} onChange={(event) => setExcerpt(event.target.value)} />
      </label>
      <Button type="button" size="sm" variant="secondary" disabled={disabled || !excerpt.trim()} onClick={() => {
        const state = useSessionStore.getState(), record = state.sessions.find((item) => item.id === session);
        if (ipc.agentBaseUrl !== loadedHost.current || state.currentSessionId !== session ||
            (state.currentProjectId ?? "inbox") !== (project ?? "inbox") ||
            (record?.project_id ?? null) !== project || (record?.engine ?? "native") !== engine) {
          setNotice("服务、会话或项目已变化，未加入交接。"); return;
        }
        setNotice(onAdd(excerpt));
      }}>加入恢复交接</Button>
    </>}
  </div>;
}

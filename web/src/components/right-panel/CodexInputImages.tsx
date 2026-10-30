import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { AgentRun, RunInputImage } from "../../types/ipc";
import { Button } from "../../ui";

/** Explicit, scoped read of one saved image; it never changes the composer. */
export function CodexInputImages({ run }: { run: AgentRun }): JSX.Element | null {
  const currentSession = useSessionStore((s) => s.currentSessionId);
  const currentProject = useSessionStore((s) => s.currentProjectId);
  const boundProject = useSessionStore((s) => s.sessions.find((item) => item.id === s.currentSessionId)?.project_id ?? null);
  const [image, setImage] = useState<RunInputImage | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const epoch = useRef(0);

  useEffect(() => {
    const clear = () => { epoch.current++; setImage(null); setBusy(false); setNotice(""); };
    clear();
    const stop = ipc.onSideCar(clear);
    return () => { epoch.current++; stop(); };
  }, [run.id, currentSession, currentProject, boundProject]);

  if (run.metadata?.engine !== "codex") return null;
  const load = async (index: number) => {
    if (busy || !currentSession || currentSession !== run.session_id || currentProject !== boundProject ||
        (run.metadata?.project_id ?? null) !== boundProject) return;
    const ticket = ++epoch.current, host = ipc.agentBaseUrl;
    const session = currentSession, project = boundProject;
    let changed = false;
    const off = useSessionStore.subscribe((state) => {
      const bound = state.sessions.find((item) => item.id === session)?.project_id ?? null;
      if (state.currentSessionId !== session || state.currentProjectId !== project || bound !== project) changed = true;
    });
    const stop = ipc.onSideCar(() => { changed = true; });
    const current = () => !changed && ticket === epoch.current && ipc.agentBaseUrl === host;
    setBusy(true); setNotice(""); setImage(null);
    try {
      const identity = await typedIPC.getServiceIdentity();
      if (!current() || !identity.service_id) throw new Error("当前服务或会话已变化");
      const result = await typedIPC.getRunInputImage({ run_id: run.id, session_id: session,
        project_id: project, expected_service_id: identity.service_id, index });
      if (!current()) return;
      if (result.run_id !== run.id || result.session_id !== session || result.project_id !== project ||
          result.service_id !== identity.service_id || result.index !== index ||
          !Number.isInteger(result.count) || result.count < 1 || result.count > 5 || index >= result.count ||
          !["image/png", "image/jpeg", "image/gif", "image/webp"].includes(result.media_type) ||
          typeof result.data !== "string" || !result.data || result.data.length > 5592408 ||
          !/^[A-Za-z0-9+/]*={0,2}$/.test(result.data)) throw new Error("图片返回的绑定或格式不一致");
      setImage(result);
    } catch (error) {
      if (current()) setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      off(); stop();
      if (ticket === epoch.current) setBusy(false);
    }
  };

  return <div className="space-y-1 text-xs text-ink-2">
    <Button type="button" size="sm" variant="secondary" disabled={busy || currentSession !== run.session_id || currentProject !== boundProject || (run.metadata?.project_id ?? null) !== boundProject}
      onClick={() => void load(0)}>查看任务输入图片</Button>
    <p>按需从当前服务读取原任务的图片，不会发送或加入草稿；旧记录或纯文本任务可能没有图片。</p>
    {notice && <p role="status" className="break-words">{notice}</p>}
    {image && image.session_id === currentSession && image.project_id === currentProject && image.project_id === boundProject && <div className="space-y-1">
      <p>第 {image.index + 1} / {image.count} 张 · {image.media_type}</p>
      <img src={`data:${image.media_type};base64,${image.data}`} alt={`任务输入图片 ${image.index + 1}`} className="max-h-80 max-w-full object-contain" />
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" variant="secondary" disabled={busy || image.index === 0} onClick={() => void load(image.index - 1)}>上一张</Button>
        <Button type="button" size="sm" variant="secondary" disabled={busy || image.index + 1 >= image.count} onClick={() => void load(image.index + 1)}>下一张</Button>
        <Button type="button" size="sm" variant="secondary" onClick={() => { epoch.current++; setImage(null); setNotice(""); }}>关闭图片</Button>
      </div>
    </div>}
  </div>;
}

import { useEffect, useState } from "react";
import { ipc } from "../../ipc";
import { useArtifactStore, useSessionStore } from "../../stores";
import { Button, Input } from "../../ui";

export function ArtifactsPanel({ onClose }: { onClose: () => void }): JSX.Element {
  const projectId = useSessionStore((state) => state.currentProjectId);
  const project = useSessionStore((state) => state.projects.find((item) => item.id === projectId));
  const state = useArtifactStore();
  const host = ipc.agentBaseUrl;
  const scoped = state.projectId === projectId && state.host === host;
  const [path, setPath] = useState("");
  useEffect(() => {
    setPath("");
    void useArtifactStore.getState().activate(projectId);
    return () => useArtifactStore.getState().clear();
  }, [projectId, host]);
  return (
    <section className="flex h-full flex-col gap-3 overflow-auto p-4" aria-label="项目文本成果">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">文本成果 · {project?.name ?? "选择项目"}</h2>
        <Button variant="ghost" onClick={onClose}>关闭</Button>
      </div>
      <p className="text-sm text-ink-1">TXT / MD / CSV，UTF-8，最大 2 MiB。每次登记创建新版本，原文件保持不变。</p>
      <p className="text-sm text-ink-1">登记和可解析状态不代表质量验证或交付完成。Markdown 按纯文本显示；CSV 不计算公式。</p>
      {ipc.isMock && <p role="status">演示模式：仅提供虚拟文件 demo.txt。</p>}
      {!project?.root_path && !ipc.isMock && <p role="status">请在当前项目设置明确的本机根目录后登记。</p>}
      <form className="flex gap-2" onSubmit={(event) => { event.preventDefault(); void state.register(path); }}>
        <Input aria-label="项目相对文件路径" value={path} onChange={(event) => setPath(event.target.value)} placeholder="reports/result.md" />
        <Button type="submit" disabled={!scoped || !path || !state.serviceId || state.busy || state.loading}>登记新版本</Button>
        <Button type="button" variant="secondary" disabled={state.busy} onClick={() => void state.activate(projectId)}>刷新</Button>
      </form>
      {scoped && state.error && <p role="alert" className="text-status-error">{state.error}</p>}
      {state.loading && <p role="status">正在读取成果…</p>}
      {!state.loading && !state.artifacts.length && <p>当前项目还没有登记的文本成果。</p>}
      <ul className="space-y-1">
        {(scoped ? state.artifacts : []).map((row) => <li key={row.id}>
          <button className="rounded border border-line p-2 text-left hover:bg-surface-3" onClick={() => void state.select(row.id)}>
            {row.source_path} · v{row.version} · {row.size_bytes} 字节 · 可解析 / 未验证
          </button>
        </li>)}
      </ul>
      {scoped && state.selected && <div className="space-y-2">
        <p className="break-all text-xs text-ink-1">SHA-256: {state.selected.sha256}</p>
        <Button disabled={state.busy} onClick={() => void state.download()}>下载原始字节</Button>
        <pre className="whitespace-pre-wrap break-words rounded border border-line bg-surface-1 p-3" aria-label="成果纯文本预览">{state.preview}</pre>
      </div>}
    </section>
  );
}

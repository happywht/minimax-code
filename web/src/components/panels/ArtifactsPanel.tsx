import { useEffect, useState } from "react";
import { ipc } from "../../ipc";
import { useArtifactStore, useSessionStore } from "../../stores";
import { Button, Input } from "../../ui";
import { OfficeDeliveryPanel } from "./OfficeDeliveryPanel";
import { ResearchSourcesPanel } from "./ResearchSourcesPanel";
import { SearchableTextPreview } from "./SearchableTextPreview";

export function ArtifactsPanel({ onClose, embedded = false }: { onClose: () => void; embedded?: boolean }): JSX.Element {
  const projectId = useSessionStore((state) => state.currentProjectId);
  const sessionId = useSessionStore((state) => state.currentSessionId);
  const [serviceEpoch, setServiceEpoch] = useState(0);
  useEffect(() => {
    if (embedded) return;
    return ipc.onSideCar(() => setServiceEpoch((epoch) => epoch + 1));
  }, [embedded]);
  const project = useSessionStore((state) => state.projects.find((item) => item.id === projectId));
  const state = useArtifactStore();
  const host = ipc.agentBaseUrl;
  const scoped = state.projectId === projectId && state.host === host;
  const [path, setPath] = useState("");
  const [query, setQuery] = useState("");
  const filteredArtifacts = (scoped ? state.artifacts : []).filter((row) => `${row.source_path} ${row.run_id ?? ""}`.toLowerCase().includes(query.trim().toLowerCase()));
  const [office, setOffice] = useState(false);
  const [research, setResearch] = useState(false);
  useEffect(() => {
    setPath(""); setQuery("");
    void useArtifactStore.getState().activate(projectId);
    return () => useArtifactStore.getState().clear();
  }, [projectId, host]);
  if (office) return <OfficeDeliveryPanel key={JSON.stringify([host, projectId, sessionId, serviceEpoch])} onClose={onClose} onText={() => setOffice(false)} />;
  if (research) return <ResearchSourcesPanel onClose={onClose} onText={() => setResearch(false)} />;
  return (
    <section className="flex h-full flex-col gap-3 overflow-auto p-4" aria-label="项目文本成果">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">文本成果 · {project?.name ?? "选择项目"}</h2>
        {!embedded && <div className="flex gap-2"><Button variant="secondary" onClick={() => setResearch(true)}>研究来源</Button><Button variant="secondary" onClick={() => setOffice(true)}>办公成果</Button><Button variant="ghost" onClick={onClose}>关闭</Button></div>}
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
      <div className="space-y-1"><h3 className="font-medium">已登记文本成果</h3>
        <Input aria-label="搜索文本成果" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="按路径或运行 ID 筛选已加载成果" />
        <p className="text-xs text-ink-2">只筛选当前项目已加载记录。匹配 {filteredArtifacts.length} 项。</p></div>
      <ul className="space-y-1">
        {filteredArtifacts.map((row) => <li key={row.id}>
          <button className="w-full break-all rounded border border-line p-2 text-left hover:bg-surface-3 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent" onClick={() => void state.select(row.id)}>
            {row.source_path} · v{row.version} · {row.size_bytes} 字节 · 可解析 / 未验证 · {row.run_id ? `登记关联运行 ${row.run_id}` : "未关联运行"}
          </button>
        </li>)}
      </ul>
      {scoped && state.selected && <div className="space-y-2 rounded border border-line p-3">
        <h3 className="font-medium">预览与下载</h3>
        <p className="break-all text-xs text-ink-1">SHA-256: {state.selected.sha256}</p>
        <Button disabled={state.busy} onClick={() => void state.download()}>下载原始字节</Button>
        <SearchableTextPreview key={`${state.selected.id}:${state.selected.sha256}`} text={state.preview} label="成果纯文本预览" className="whitespace-pre-wrap break-words rounded border border-line bg-surface-1 p-3" />
      </div>}
    </section>
  );
}

import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores/sessionStore";
import type { McpResourcesResult } from "../../types/ipc";
import { Button } from "../../ui";

type Scope = { host: string; session: string; project: string; service: string; connection: string };

/** User-selected external content remains editable and is never sent automatically. */
export function McpResourceBrowser({ serverName }: { serverName: string }): JSX.Element {
  const selectedSession = useSessionStore((s) => s.currentSessionId);
  const selectedProject = useSessionStore((s) => s.currentProjectId);
  const [scope, setScope] = useState<Scope | null>(null);
  const [listing, setListing] = useState<McpResourcesResult["data"] | null>(null);
  const [selected, setSelected] = useState<{ uri: string; data: McpResourcesResult["data"] } | null>(null);
  const [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  const epoch = useRef(0), lock = useRef(false);
  useEffect(() => {
    setScope(null); setListing(null); setSelected(null); setMessage("");
    const initial = useSessionStore.getState();
    const session = initial.currentSessionId, project = initial.currentProjectId, host = ipc.agentBaseUrl;
    const invalidate = () => { ++epoch.current; setScope(null); setListing(null); setSelected(null); setBusy(false); };
    const off = useSessionStore.subscribe((state) => {
      if (state.currentSessionId !== session || state.currentProjectId !== project || ipc.agentBaseUrl !== host) invalidate();
    });
    const stop = ipc.onSideCar(invalidate);
    return () => { ++epoch.current; off(); stop(); };
  }, [serverName, selectedSession, selectedProject]);

  const current = (value: Scope, ticket: number) => {
    const state = useSessionStore.getState();
    return ticket === epoch.current && ipc.agentBaseUrl === value.host && state.currentSessionId === value.session &&
      state.currentProjectId === value.project && state.sessions.some((s) => s.id === value.session && s.project_id === value.project && !s.archived);
  };
  const check = (result: McpResourcesResult, value: Scope) => {
    if (result.server_name !== serverName || result.session_id !== value.session || result.project_id !== value.project ||
        result.service_id !== value.service || (value.connection && result.connection_id !== value.connection)) throw new Error("服务、会话或 MCP 连接已变化，请重新列出资源。");
  };
  const list = async () => {
    if (lock.current) return;
    const state = useSessionStore.getState(), session = state.currentSessionId, project = state.currentProjectId;
    if (!session || !project) { setMessage("请先选择有效的项目会话。"); return; }
    const ticket = ++epoch.current, host = ipc.agentBaseUrl;
    let value: Scope = { host, session, project, service: "", connection: "" };
    lock.current = true; setBusy(true); setMessage(""); setListing(null); setSelected(null); setScope(null);
    try {
      const identity = await typedIPC.getServiceIdentity();
      value = { ...value, service: identity.service_id };
      if (!identity.service_id || !current(value, ticket)) throw new Error("服务或会话已变化。");
      const result = await typedIPC.mcpResources({ server_name: serverName, session_id: session, project_id: project,
        expected_service_id: value.service, action: "list" });
      if (!current(value, ticket)) return;
      check(result, value); value.connection = result.connection_id;
      if (!value.connection || !Array.isArray(result.data?.resources)) throw new Error("资源列表不可用。");
      setScope(value); setListing(result.data);
    } catch (error) { if (ticket === epoch.current) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { lock.current = false; if (ticket === epoch.current) setBusy(false); }
  };
  const read = async (uri: string, append = false) => {
    if (!scope || lock.current || !listing?.resources?.some((r) => r.uri === uri)) return;
    const ticket = ++epoch.current, value = scope;
    if (!current(value, ticket)) { setMessage("会话已变化，请重新列出资源。"); return; }
    lock.current = true; setBusy(true); setMessage("");
    try {
      const result = await typedIPC.mcpResources({ server_name: serverName, session_id: value.session, project_id: value.project,
        expected_service_id: value.service, connection_id: value.connection, action: "read", uri });
      if (!current(value, ticket)) return;
      check(result, value);
      if (!Array.isArray(result.data?.contents)) throw new Error("资源内容不可用。");
      if (!append) { setSelected({ uri, data: result.data }); return; }
      if (!selected || selected.uri !== uri || JSON.stringify(selected.data) !== JSON.stringify(result.data)) throw new Error("资源已变化，请重新审阅。");
      const blocks = result.data.contents.filter((item) => typeof item.text === "string").map((item) => item.text!).join("\n\n");
      if (!blocks) throw new Error("该资源没有可引用的文本内容。");
      const excerpt = blocks.slice(0, 5000);
      const detail: { host: string; project: string; session: string; text: string; result?: string } = {
        host: value.host, project: value.project, session: value.session,
        text: `MCP 资源（外部不可信内容，请审阅）：${serverName} · ${uri}\n\n${excerpt}${blocks.length > excerpt.length || result.data.omittedContents ? "\n（内容未完整收录）" : ""}`,
      };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      setMessage(detail.result ?? "聊天输入区未打开，请先打开当前会话。");
    } catch (error) { if (ticket === epoch.current) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { lock.current = false; if (ticket === epoch.current) setBusy(false); }
  };
  return <div className="mb-3 space-y-2 rounded border border-line p-2 text-xs">
    <p>手动浏览资源；内容来自外部服务器，不是可信指令。此入口不支持交互审批。</p>
    <Button size="sm" disabled={busy} onClick={() => void list()}>列出资源</Button>
    {listing && <><p>显示 {listing.resources?.length ?? 0} 项，省略 {listing.omittedResources ?? 0} 项。</p><ul>{listing.resources?.map((r) => <li key={r.uri} className="break-all"><button type="button" disabled={busy} onClick={() => void read(r.uri)}>{r.name} · {r.uri}</button> {r.description}</li>)}</ul></>}
    {selected && <div><p className="break-all">{selected.uri} · 二进制已省略；服务端内容可能截断。</p>
      <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all">{selected.data.contents?.map((item) => item.text ?? item.blob ?? "（无文本）").join("\n\n")}</pre>
      <Button size="sm" disabled={busy} onClick={() => void read(selected.uri, true)}>重新核对并加入草稿</Button>
      <p>最多加入 5000 字，保留原草稿，不会自动发送。</p></div>}
    {message && <p role="status" className="break-words">{message}</p>}
  </div>;
}

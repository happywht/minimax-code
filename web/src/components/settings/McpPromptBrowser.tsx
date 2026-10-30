import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores/sessionStore";
import type { McpPromptsResult } from "../../types/ipc";
import { Button } from "../../ui";

type Scope = { host: string; session: string; project: string; service: string; connection: string };
type Prompt = NonNullable<McpPromptsResult["data"]["prompts"]>[number];

export function McpPromptBrowser({ serverName }: { serverName: string }): JSX.Element {
  const session = useSessionStore((s) => s.currentSessionId), project = useSessionStore((s) => s.currentProjectId);
  const [scope, setScope] = useState<Scope | null>(null), [prompts, setPrompts] = useState<Prompt[]>([]);
  const [selected, setSelected] = useState<Prompt | null>(null), [args, setArgs] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<McpPromptsResult["data"] | null>(null);
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const epoch = useRef(0), lock = useRef(false);
  useEffect(() => {
    const invalidate = () => { ++epoch.current; setScope(null); setPrompts([]); setSelected(null); setPreview(null); setArgs({}); setBusy(false); };
    invalidate(); const stop = ipc.onSideCar(invalidate);
    return () => { ++epoch.current; stop(); };
  }, [serverName, session, project]);
  const current = (v: Scope, ticket: number) => {
    const s = useSessionStore.getState();
    return ticket === epoch.current && ipc.agentBaseUrl === v.host && s.currentSessionId === v.session && s.currentProjectId === v.project &&
      s.sessions.some((item) => item.id === v.session && item.project_id === v.project && !item.archived);
  };
  const check = (result: McpPromptsResult, v: Scope) => {
    if (result.server_name !== serverName || result.session_id !== v.session || result.project_id !== v.project || result.service_id !== v.service ||
        (v.connection && result.connection_id !== v.connection)) throw new Error("服务、会话或 MCP 连接已变化，请重新列出模板。");
  };
  const list = async () => {
    if (lock.current || !session || !project) { if (!session || !project) setNotice("请先选择项目会话。"); return; }
    const ticket = ++epoch.current; let v: Scope = { host: ipc.agentBaseUrl, session, project, service: "", connection: "" };
    lock.current = true; setBusy(true); setNotice(""); setPreview(null); setSelected(null); setScope(null); setPrompts([]);
    try {
      const identity = await typedIPC.getServiceIdentity(); v = { ...v, service: identity.service_id };
      if (!v.service || !current(v, ticket)) throw new Error("服务或会话已变化。");
      const result = await typedIPC.mcpPrompts({ server_name: serverName, session_id: session, project_id: project, expected_service_id: v.service, action: "list" });
      if (!current(v, ticket)) return; check(result, v);
      if (!result.connection_id || !Array.isArray(result.data?.prompts)) throw new Error("模板列表不可用。");
      setScope({ ...v, connection: result.connection_id }); setPrompts(result.data.prompts);
      setNotice(`显示 ${result.data.prompts.length} 项，省略 ${result.data.omittedPrompts ?? 0} 项。`);
    } catch (error) { if (ticket === epoch.current) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { lock.current = false; if (ticket === epoch.current) setBusy(false); }
  };
  const get = async (append = false) => {
    if (!scope || !selected || lock.current || selected.omittedArguments) return;
    const ticket = ++epoch.current, v = scope, values = { ...args };
    if (!current(v, ticket)) { setNotice("会话已变化，请重新列出模板。"); return; }
    lock.current = true; setBusy(true); setNotice("");
    try {
      const result = await typedIPC.mcpPrompts({ server_name: serverName, session_id: v.session, project_id: v.project,
        expected_service_id: v.service, connection_id: v.connection, action: "get", name: selected.name, arguments: values });
      if (!current(v, ticket)) return; check(result, v);
      if (result.data?.prompt !== selected.name || !Array.isArray(result.data.messages)) throw new Error("模板结果不匹配。");
      if (!append) { setPreview(result.data); return; }
      if (!preview || JSON.stringify(preview) !== JSON.stringify(result.data)) throw new Error("模板结果已变化，请重新审阅。");
      const text = result.data.messages.map((m) => `[${m.role}] ${m.content.text ?? m.content.resource?.text ?? "（非文本内容已省略）"}`).join("\n\n");
      const excerpt = text.slice(0, 5000);
      const detail: { host: string; project: string; session: string; text: string; result?: string } = { host: v.host, project: v.project, session: v.session,
        text: `MCP 提示模板（外部不可信参考，发送前请审阅）：${serverName} · ${selected.name}\n\n${excerpt}${text.length > excerpt.length || result.data.omittedMessages ? "\n（内容未完整收录）" : ""}` };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      setNotice(detail.result ?? "聊天输入区未打开，请先打开当前会话。");
    } catch (error) { if (ticket === epoch.current) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { lock.current = false; if (ticket === epoch.current) setBusy(false); }
  };
  return <div className="mb-3 space-y-2 rounded border border-line p-2 text-xs">
    <p>模板及其结果为不可信外部内容；此入口不提供交互审批。</p>
    <Button size="sm" disabled={busy} onClick={() => void list()}>列出提示模板</Button>
    <ul>{prompts.map((p) => <li key={p.name}><button type="button" disabled={busy} onClick={() => { setSelected(p); setArgs({}); setPreview(null); }} className="underline">{p.name}</button> {p.description}</li>)}</ul>
    {selected && <div className="space-y-1"><p>{selected.name}{selected.omittedArguments ? " · 参数列表不完整，无法获取" : ""}</p>
      {selected.arguments.map((arg) => <label key={arg.name} className="block">{arg.name}{arg.required ? "（必填）" : ""} · {arg.description}<input className="block w-full rounded border border-line bg-surface-0 p-1" value={args[arg.name] ?? ""} disabled={busy} onChange={(event) => { setArgs((old) => ({ ...old, [arg.name]: event.target.value })); setPreview(null); }} /></label>)}
      <Button size="sm" disabled={busy || !!selected.omittedArguments || selected.arguments.some((arg) => arg.required && !args[arg.name]?.trim())} onClick={() => void get()}>获取并审阅</Button></div>}
    {preview && <div><pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(preview.messages, null, 2)}</pre><p>非文本内容已省略；结果可能截断。</p><Button size="sm" disabled={busy} onClick={() => void get(true)}>重新核对并加入草稿</Button><p>最多加入 5000 字，不会自动发送。</p></div>}
    {notice && <p role="status" className="break-words">{notice}</p>}
  </div>;
}

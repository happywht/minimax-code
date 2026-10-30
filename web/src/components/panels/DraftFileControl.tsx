import { useEffect, useRef, useState } from "react";
import { ipc } from "../../ipc";
import { useSessionStore } from "../../stores";
import { Button } from "../../ui";

type Scope = { host: string; service: string; project: string; session: string | null };
type Props<T> = { kind: "report" | "table" | "report_delivery"; scope: Scope | null; disabled?: boolean;
  capture: () => T; decode: (value: unknown, scope: Scope) => Promise<T>; apply: (value: T) => void };
const LIMIT = 6 * 1024 * 1024;

/** Explicit local draft export/import. Neither operation submits a task or writes a project file. */
export function DraftFileControl<T>({ kind, scope, disabled, capture, decode, apply }: Props<T>): JSX.Element {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const latest = useRef({ scope, capture, decode, apply });
  latest.current = { scope, capture, decode, apply };
  const epoch = useRef(0);
  const scopeKey = JSON.stringify(scope);
  useEffect(() => { epoch.current++; setBusy(false); setMessage(""); }, [scopeKey]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; setBusy(false); setMessage("连接发生变化，请重新确认服务后再恢复草稿。"); }), []);
  useEffect(() => () => { epoch.current++; }, []);
  const sameScope = (expected: Scope) => {
    const current = latest.current.scope, session = useSessionStore.getState();
    return !!current && JSON.stringify(current) === JSON.stringify(expected) && ipc.agentBaseUrl === expected.host &&
      session.currentProjectId === expected.project && session.currentSessionId === expected.session;
  };
  const exportDraft = () => {
    if (!scope || disabled || !sameScope(scope)) return;
    try {
      const json = JSON.stringify({ version: 1, kind, scope, data: capture() }, null, 2);
      if (new TextEncoder().encode(json).length > LIMIT) throw new Error("草稿超过 6 MiB，无法导出。");
      const url = URL.createObjectURL(new Blob([json], { type: "application/json" }));
      const anchor = document.createElement("a"); anchor.href = url;
      anchor.download = `${kind}-draft-${crypto.randomUUID()}.json`;
      anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      setMessage("已交给浏览器下载；文件包含草稿内容，请自行妥善保管。");
    } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
  };
  const importDraft = async (file: File) => {
    if (!scope || disabled || busy || !sameScope(scope)) return;
    const ticket = ++epoch.current, expected = scope, before = JSON.stringify(capture());
    const current = () => ticket === epoch.current && sameScope(expected) && JSON.stringify(latest.current.capture()) === before;
    setBusy(true); setMessage("");
    try {
      if (file.size > LIMIT) throw new Error("草稿文件超过 6 MiB。");
      const bytes = await file.arrayBuffer();
      if (!current()) throw new Error("上下文或当前草稿已变化，未恢复。");
      const envelope: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
      if (!envelope || typeof envelope !== "object") throw new Error("草稿格式无效。");
      const item = envelope as { version?: unknown; kind?: unknown; scope?: unknown; data?: unknown };
      if (item.version !== 1 || item.kind !== kind || JSON.stringify(item.scope) !== JSON.stringify(expected))
        throw new Error("草稿类型、项目、会话或服务不匹配；未恢复。");
      const decoded = await latest.current.decode(item.data, expected);
      if (!current()) throw new Error("上下文或当前草稿已变化，未恢复。");
      if (!window.confirm("恢复会替换当前编辑内容。请先保存需要保留的草稿；确定恢复吗？")) { setMessage("已保留当前草稿。"); return; }
      if (!current()) throw new Error("上下文或当前草稿已变化，未恢复。");
      latest.current.apply(decoded);
      setMessage("草稿已恢复；尚未生成或提交，请核对内容。");
    } catch (error) { if (ticket === epoch.current) setMessage(`未恢复草稿：${error instanceof Error ? error.message : String(error)}`); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  return <div className="space-y-2 rounded border border-line bg-surface-1 p-3 text-xs">
    <p className="font-medium">手动保存或恢复草稿</p>
    <p className="text-ink-2">仅本地文件；包含实际编辑内容，可能有敏感信息。恢复仅支持同一服务、项目及会话，不会自动提交或修改原件。</p>
    <div className="flex flex-wrap items-center gap-2"><Button type="button" size="sm" variant="secondary" disabled={!scope || disabled || busy} onClick={exportDraft}>导出草稿</Button>
      <label className="text-ink-1">选择草稿文件 <input aria-label={kind === "report" ? "选择报告草稿" : kind === "table" ? "选择表格草稿" : "选择报告交付说明草稿"} type="file" accept=".json,application/json" disabled={!scope || disabled || busy} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void importDraft(file); }} /></label></div>
    {busy && <p role="status">正在读取并核对草稿…</p>}{message && <p role="status">{message}</p>}
  </div>;
}

import { useEffect, useRef, useState } from "react";
import { ipc } from "../../ipc";
import { useSessionStore } from "../../stores";
import { useOfficeDeliveryStore } from "../../stores/officeDeliveryStore";
import type { OfficeTableSpec } from "../../types/ipc";
import { Button } from "../../ui";
import { csvToOfficeSpec, parseOfficeCsv, type CsvTable } from "./officeCsv";

type Kind = OfficeTableSpec["columns"][number]["kind"];
type RightTable = { name: string; sha256: string; table: CsvTable };
export function CsvLookupJoin({ table, kinds, onApply }: { table: CsvTable; kinds: Kind[]; onApply: (table: CsvTable, kinds: Kind[]) => void }): JSX.Element {
  const project = useSessionStore((state) => state.currentProjectId), session = useSessionStore((state) => state.currentSessionId);
  const service = useOfficeDeliveryStore((state) => state.serviceId), host = ipc.agentBaseUrl;
  const [right, setRight] = useState<RightTable | null>(null), [leftKey, setLeftKey] = useState(0), [rightKey, setRightKey] = useState(0);
  const [additionalKeys, setAdditionalKeys] = useState<{ left: number; right: number }[]>([]);
  const [columns, setColumns] = useState<number[]>([]), [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const epoch = useRef(0), base = useRef({ table, revision: 0 });
  if (base.current.table !== table) base.current = { table, revision: base.current.revision + 1 };
  useEffect(() => { epoch.current++; setRight(null); setLeftKey(0); setRightKey(0); setAdditionalKeys([]); setColumns([]); setBusy(false); setNotice("");
    return () => { epoch.current++; }; }, [project, session, service, host]);
  useEffect(() => { setLeftKey(0); setAdditionalKeys([]); }, [table.headers]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; setRight(null); setBusy(false); setNotice("连接变化，请重新选择文件。"); }), []);
  const scoped = () => {
    const active = useSessionStore.getState(), office = useOfficeDeliveryStore.getState();
    return !!project && !!session && !!service && ipc.agentBaseUrl === host && active.currentProjectId === project && active.currentSessionId === session &&
      office.projectId === project && office.host === host && office.serviceId === service;
  };
  const load = async (file: File) => {
    const ticket = ++epoch.current, revision = base.current.revision;
    const current = () => {
      const active = useSessionStore.getState(), office = useOfficeDeliveryStore.getState();
      return ticket === epoch.current && base.current.revision === revision && ipc.agentBaseUrl === host && active.currentProjectId === project &&
        active.currentSessionId === session && office.projectId === project && office.host === host && office.serviceId === service;
    };
    setBusy(true); setNotice("");
    try {
      if (!project || !session || !service || !current()) throw new Error("请先确认当前项目、会话和服务");
      if (file.size > 1048576) throw new Error("第二个 CSV 超过 1 MiB");
      const buffer = await file.arrayBuffer();
      if (!current()) throw new Error("表格或归属已变化，未载入");
      if (buffer.byteLength !== file.size || buffer.byteLength > 1048576) throw new Error("第二个 CSV 文件大小已变化");
      const text = new TextDecoder("utf-8", { fatal: true }).decode(buffer), parsed = parseOfficeCsv(text);
      const digest = await crypto.subtle.digest("SHA-256", buffer);
      if (!current()) throw new Error("表格或归属已变化，未载入");
      const sha256 = Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
      setRight({ name: file.name, sha256, table: parsed }); setRightKey(0); setAdditionalKeys([]); setColumns([]);
      setNotice("已读取本地 CSV；未上传或修改该文件。请选择键和要追加的列。");
    } catch (error) { if (ticket === epoch.current) setNotice(`${error instanceof Error ? error.message : String(error)}；已加载的右表保持不变。`); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  let result: { table: CsvTable; matched: number; unmatched: number; skippedRight: number } | null = null, error = "";
  if (right) try {
    const pairs = [{ left: leftKey, right: rightKey }, ...additionalKeys];
    if (pairs.some((pair) => !Number.isInteger(pair.left) || pair.left < 0 || pair.left >= table.headers.length ||
        !Number.isInteger(pair.right) || pair.right < 0 || pair.right >= right.table.headers.length) || !columns.length) throw new Error("请选择有效的键和至少一列");
    if (new Set(pairs.map((pair) => pair.left)).size !== pairs.length || new Set(pairs.map((pair) => pair.right)).size !== pairs.length)
      throw new Error("每侧的键列不能重复，请调整匹配字段");
    const headers = [...table.headers, ...columns.map((index) => right.table.headers[index])];
    if (headers.length > 20 || new Set(headers.map((name) => name.toLowerCase())).size !== headers.length) throw new Error("结果超过 20 列或列名重复，请先重命名或调整选择");
    const lookup = new Map<string, string[]>(); let skippedRight = 0;
    for (const row of right.table.rows) {
      const values = pairs.map((pair) => row[pair.right]);
      if (values.some((value) => value === "")) { skippedRight++; continue; }
      const key = JSON.stringify(values);
      if (lookup.has(key)) throw new Error("第二个表存在重复的完整非空键组合，无法唯一匹配；请先清理后重新选择文件");
      lookup.set(key, row);
    }
    let matched = 0;
    const rows = table.rows.map((row) => {
      const values = pairs.map((pair) => row[pair.left]);
      const found = values.some((value) => value === "") ? undefined : lookup.get(JSON.stringify(values));
      if (found) matched++;
      return [...row, ...columns.map((index) => found ? found[index] : "")];
    });
    const next = { headers, rows };
    csvToOfficeSpec(next, [...kinds, ...columns.map(() => "text" as const)]);
    result = { table: next, matched, unmatched: table.rows.length - matched, skippedRight };
  } catch (reason) { error = reason instanceof Error ? reason.message : "无法匹配"; }
  return <fieldset className="space-y-2 rounded border border-line p-2">
    <legend>从另一 CSV 按键追加列</legend>
    <p>按 1–3 对字段匹配 CSV；空键不匹配，右表重复键会拒绝。保留当前行顺序，未匹配留空；不执行公式。</p>
    <details className="text-xs text-ink-2"><summary>查看匹配规则</summary>
      <p>右表为 UTF-8 CSV，最多 1 MiB、1000 行和 20 列。匹配区分大小写，不去空白或转换类型。</p>
      <p>当前表重复键可匹配同一右表行；右表多余行忽略，追加列默认为文本。</p>
    </details>
    <label>选择右表 CSV <input aria-label="选择匹配右表 CSV" type="file" accept=".csv,text/csv" disabled={busy || !project || !session || !service} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void load(file); }} /></label>
    {busy && <p role="status">正在读取本地文件…</p>}{notice && <p role="status">{notice}</p>}
    {right && <><p className="break-all">右表：{right.name} · SHA-256 {right.sha256} · {right.table.rows.length} 行。文件不上传；后续若需交付处理口径，请将此身份及规则填写到成果处理说明。</p>
      <Button type="button" variant="ghost" disabled={busy} onClick={() => { setRight(null); setColumns([]); setNotice("已清除右表，不影响当前草稿。"); }}>清除右表</Button>
      <div className="flex flex-wrap gap-2"><label>当前表键 <select aria-label="匹配当前表键" value={leftKey} onChange={(event) => setLeftKey(Number(event.target.value))}>{table.headers.map((name, i) => <option key={i} value={i}>第 {i + 1} 列（{name || "未命名"}）</option>)}</select></label>
        <label>右表键 <select aria-label="匹配右表键" value={rightKey} onChange={(event) => setRightKey(Number(event.target.value))}>{right.table.headers.map((name, i) => <option key={i} value={i}>第 {i + 1} 列（{name || "未命名"}）</option>)}</select></label></div>
      {additionalKeys.map((pair, index) => <div key={index} className="flex flex-wrap gap-2"><label>当前表附加键 {index + 1} <select aria-label={`当前表附加键 ${index + 1}`} value={pair.left} onChange={(event) => { const left = Number(event.target.value); setAdditionalKeys((old) => old.map((item, i) => i === index ? { ...item, left } : item)); }}>{table.headers.map((name, i) => <option key={i} value={i}>第 {i + 1} 列（{name || "未命名"}）</option>)}</select></label>
        <label>右表附加键 {index + 1} <select aria-label={`右表附加键 ${index + 1}`} value={pair.right} onChange={(event) => { const next = Number(event.target.value); setAdditionalKeys((old) => old.map((item, i) => i === index ? { ...item, right: next } : item)); }}>{right.table.headers.map((name, i) => <option key={i} value={i}>第 {i + 1} 列（{name || "未命名"}）</option>)}</select></label>
        <Button type="button" variant="ghost" onClick={() => setAdditionalKeys((old) => old.filter((_, i) => i !== index))}>删除附加键</Button></div>)}
      <Button type="button" variant="secondary" disabled={additionalKeys.length >= 2 || additionalKeys.length + 1 >= Math.min(table.headers.length, right.table.headers.length)} onClick={() => {
        const left = table.headers.findIndex((_, index) => index !== leftKey && !additionalKeys.some((pair) => pair.left === index));
        const rightColumn = right.table.headers.findIndex((_, index) => index !== rightKey && !additionalKeys.some((pair) => pair.right === index));
        if (left >= 0 && rightColumn >= 0) setAdditionalKeys((old) => [...old, { left, right: rightColumn }]);
      }}>添加匹配字段</Button>
      <fieldset><legend>选择追加列</legend>{right.table.headers.map((name, index) => <label key={index} className="mr-3 inline-block"><input type="checkbox" checked={columns.includes(index)} onChange={(event) => { const checked = event.target.checked;
        setColumns((previous) => checked ? [...previous, index].sort((a, b) => a - b) : previous.filter((value) => value !== index)); }} /> 第 {index + 1} 列（{name}）</label>)}</fieldset>
      <p role="status">{error || (result && `预览：${result.matched} 行匹配，${result.unmatched} 行未匹配（含不完整键）；右表 ${result.skippedRight} 行因键不完整未参与。`)}</p>
      {result && <details><summary>预览表头和前 5 行结果</summary><pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words">{[result.table.headers, ...result.table.rows.slice(0, 5)].map((row) => JSON.stringify(row)).join("\n")}</pre></details>}
      <Button type="button" variant="secondary" disabled={busy || !result} onClick={() => { if (!scoped()) { setNotice("项目、会话或服务已变化，未修改草稿。"); return; }
        if (result) { onApply(result.table, [...kinds, ...columns.map(() => "text" as const)]); setNotice("已追加静态文本列；可撤销，需转换后生成新 XLSX。请核对匹配口径和结果。"); } }}>应用匹配结果到草稿</Button>
    </>}
  </fieldset>;
}

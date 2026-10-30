import { useState } from "react";
import type { AgentRunStep } from "../../types/ipc";

const groups = [["added", "新增"], ["modified", "修改"], ["deleted", "删除"]] as const;
type Paths = Record<(typeof groups)[number][0], string[]>;

function readPaths(payload: Record<string, unknown>): Paths | null {
  const paths = {} as Paths;
  for (const [key] of groups) {
    const value = payload[key];
    if (!Array.isArray(value) || value.length > 1000 || !value.every((path) => typeof path === "string")) return null;
    paths[key] = value;
  }
  return paths;
}

/** Persisted observations are bounded samples, never a claim of task authorship. */
export function RunChangeEvidence({ steps, workspace }: { steps: AgentRunStep[]; workspace: string | null }): JSX.Element {
  const [query, setQuery] = useState("");
  const observations = steps.filter((step) => step.kind === "observation" && step.status === "completed" && step.payload?.source === "run_file_changes");
  const needle = query.trim().toLocaleLowerCase();
  return <section className="mt-2 space-y-2 rounded border border-line p-2 text-xs" aria-label="任务前后文件采样">
    <h4 className="font-medium">任务前后文件采样</h4>
    <p className="break-all">采样绑定目录：{workspace || "未记录"}</p>
    <p className="text-ink-2">比较执行前后的有界采样，只记录路径，不保存文件内容或差异。结果可能包含用户或其他任务的同期修改，不能证明由此任务产生；忽略文件和私有运行数据不在范围内。</p>
    {!observations.length ? <p>没有已保存的采样记录；可能未启用、尚未完成或记录未保存，无法判断文件是否变化。</p> : <>
      <label className="block">筛选采样路径<input aria-label="筛选采样路径" value={query} maxLength={200} onChange={(event) => setQuery(event.target.value)}
        className="mt-1 block w-full rounded border border-line bg-surface-0 p-1" placeholder="输入路径关键词" /></label>
      {observations.map((step, index) => {
        const payload = step.payload!, paths = payload.available === true ? readPaths(payload) : null;
        return <div key={step.id} className="space-y-1 rounded bg-surface-1 p-2">
          {observations.length > 1 && <p className="font-medium">采样记录 {index + 1} · 步骤 {step.ordinal}</p>}
          {payload.available === false ? <p>采样不可用；无法判断文件是否变化。</p> : !paths ? <p role="status" className="text-status-warning">采样记录不完整或格式无法确认，无法判断文件是否变化。</p> : <>
            {groups.map(([key, label]) => {
              const matching = paths[key].filter((path) => path.toLocaleLowerCase().includes(needle));
              return <details key={key}><summary className="cursor-pointer">{label}：{paths[key].length} 项{needle ? ` · 匹配 ${matching.length} 项` : ""}</summary>
                {matching.length ? <ul className="max-h-48 overflow-auto">{matching.map((path, i) => <li className="break-all" key={`${i}:${path}`}>{path}</li>)}</ul> : <p>{needle ? "没有匹配路径。" : "此类没有记录的路径。"}</p>}
              </details>;
            })}
            {!groups.some(([key]) => paths[key].length > 0) && <p>在采样范围内没有检测到路径变化；不代表工作区没有其他变化。</p>}
          </>}
        </div>;
      })}
    </>}
  </section>;
}

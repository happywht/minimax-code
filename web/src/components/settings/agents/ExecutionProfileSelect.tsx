import type { ExecutionProfileCapability } from "../../../types/ipc";

export function ExecutionProfileSelect({ id, value, profiles, onChange }: {
  id: string;
  value: string;
  profiles: ExecutionProfileCapability[];
  onChange: (value: string) => void;
}): JSX.Element {
  const selected = profiles.find((profile) => profile.id === value);
  const known = new Set(profiles.map((profile) => profile.id));
  return <div className="space-y-1">
    <label htmlFor={id} className="block text-xs text-ink-2">执行环境</label>
    <select id={id} data-testid={id} value={value} onChange={(event) => onChange(event.target.value)}
      className="w-full rounded-md border border-line bg-surface-1 px-2 py-1 text-xs text-ink-0">
      <option value="">默认（根任务使用宿主，子任务继承父级）</option>
      {!known.has("native-host") && <option value="native-host">Native 宿主</option>}
      {profiles.map((profile) => <option key={profile.id} value={profile.id} disabled={!profile.available}>
        {profile.label}{!profile.available ? "（不可用）" : ""}
      </option>)}
      {value && !known.has(value) && value !== "native-host" &&
        <option value={value}>已保存：{value}（未知或未配置）</option>}
    </select>
    {value && (!selected || !selected.available) && value !== "native-host" &&
      <p role="status" className="text-xs text-status-warning">{selected?.reason ?? "当前服务不支持此配置；已保存值会保留。"}</p>}
    {selected?.mode === "podman" && <p className="text-xs text-ink-2">{selected.boundary_summary}；需要人工监督，不支持定时、Webhook 或团队任务。</p>}
  </div>;
}

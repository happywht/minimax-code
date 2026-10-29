import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import type { DataExportEnvelope } from "../../types/ipc";
import { Button, Panel } from "../../ui";

/** A read-only extract, deliberately distinct from the replace-import format. */
export function TableArchive({ disabled }: { disabled: boolean }): JSX.Element {
  const [snapshot, setSnapshot] = useState<DataExportEnvelope | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState("");
  const epoch = useRef(0);

  useEffect(() => {
    const off = ipc.onSideCar(() => {
      ++epoch.current;
      setSnapshot(null);
      setSelected([]);
      setLoading(false);
      setNotice("服务连接已变化，请重新读取表清单。");
    });
    return () => { ++epoch.current; off(); };
  }, []);

  const load = async () => {
    const ticket = ++epoch.current;
    const host = ipc.agentBaseUrl;
    setSnapshot(null);
    setSelected([]);
    setLoading(true);
    setNotice("");
    try {
      const result = await typedIPC.exportData();
      if (ticket !== epoch.current || host !== ipc.agentBaseUrl) return;
      setSnapshot(result);
    } catch (error) {
      if (ticket === epoch.current) setNotice(`读取失败：${String(error)}`);
    } finally {
      if (ticket === epoch.current) setLoading(false);
    }
  };

  const download = () => {
    if (!snapshot || selected.length === 0) return;
    const tables = Object.fromEntries(selected.map((name) => [name, snapshot.tables[name]]));
    const archive = {
      format: "minimax-code-table-extract",
      schema_version: snapshot.schema_version,
      app_version: snapshot.app_version,
      exported_at: snapshot.exported_at,
      tables,
    };
    const url = URL.createObjectURL(new Blob([JSON.stringify(archive, null, 2)], { type: "application/json" }));
    try {
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `minimax-code-tables-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
      anchor.click();
      setNotice(`已请求下载 ${selected.length} 张表的查阅副本。`);
    } finally {
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
  };

  return (
    <Panel title="按表导出查阅副本">
      <p className="text-[11px] text-ink-2">
        读取当前数据库后选择需要的表，下载 JSON 供查阅或整理。各表依次读取，活动数据可能前后不一致。副本不可通过本应用导入，
        不包含附件、成果原件或检查点文件，也不能代替完整备份。表中可能包含个人信息、配置和凭据，请妥善保存。
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" disabled={disabled} loading={loading} onClick={() => void load()}>
          {snapshot ? "重新读取表清单" : "读取表清单"}
        </Button>
        <Button size="sm" disabled={disabled || loading || selected.length === 0} onClick={download}>
          下载所选表（{selected.length}）
        </Button>
        {snapshot && <Button size="sm" variant="ghost" onClick={() => { setSnapshot(null); setSelected([]); setNotice(""); }}>清除已读取数据</Button>}
      </div>
      {notice && <p role="status" className="mt-2 break-all text-xs text-ink-1">{notice}</p>}
      {snapshot && <div className="mt-3">
        <p className="text-[11px] text-ink-2">导出时间：{snapshot.exported_at}；再次读取前不会反映后续更改。</p>
        <fieldset className="mt-2 max-h-64 overflow-auto rounded-md border border-line p-2">
          <legend className="text-xs text-ink-1">选择要导出的表</legend>
          {Object.keys(snapshot.tables).sort().map((name) => (
            <label key={name} className="flex items-center gap-2 py-1 text-xs text-ink-1">
              <input type="checkbox" checked={selected.includes(name)} onChange={(event) => {
                const checked = event.target.checked;
                setSelected((current) => checked ? [...current, name] : current.filter((item) => item !== name));
              }} />
              <span className="break-all">{name}（{snapshot.tables[name].length} 行）</span>
            </label>
          ))}
        </fieldset>
      </div>}
    </Panel>
  );
}

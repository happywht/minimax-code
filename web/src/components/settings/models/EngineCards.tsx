/**
 * Engine cards — M4 engine registry rendered for the settings page.
 *
 * Each engine shows its availability, version and capability chips.
 * Unavailable engines carry the *reason* (M4: 不可用要在 UI 明确呈现,
 * 绝不静默回退) and are visually disabled.
 */
import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, RefreshCw, Server } from "lucide-react";
import { Button, Spinner } from "../../../ui";
import { typedIPC } from "../../../ipc";
import type { EngineInfo, ListEnginesResult } from "../../../types/ipc";

const CAPABILITY_LABELS: Record<string, string> = {
  streaming: "流式",
  resume: "恢复",
  cancel: "取消",
  approvals: "审批",
  usage: "用量",
  steer: "中途补充",
};

export function EngineCards(): JSX.Element {
  const [engines, setEngines] = useState<EngineInfo[]>([]);
  const [defaultEngine, setDefaultEngine] = useState<string>("native");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r: ListEnginesResult = await typedIPC.listEngines();
      setEngines(r.engines);
      setDefaultEngine(r.default);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <div
      data-testid="settings-engine-cards"
      className="rounded-lg border border-line bg-panel p-4"
    >
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-medium text-fg">
          <Server className="h-4 w-4" />
          Agent 引擎
        </div>
        <Button
          size="sm"
          variant="ghost"
          data-testid="settings-engines-refresh"
          onClick={() => void refresh()}
          loading={loading}
          icon={<RefreshCw />}
        >
          刷新
        </Button>
      </div>
      <p className="mb-3 text-xs text-ink-2">
        引擎决定任务由哪个 agent 运行时执行。任务发起时锁定引擎，运行中不切换；不可用的引擎会标明原因。
        {defaultEngine && (
          <span className="ml-1">
            默认引擎：<span className="text-fg">{defaultEngine}</span>。
          </span>
        )}
      </p>
      {error && <p role="alert" className="text-xs text-status-error">引擎列表加载失败：{error}</p>}
      {loading && engines.length === 0 ? (
        <Spinner />
      ) : (
        <div className="grid gap-3">
          {engines.map((engine) => (
            <EngineCard key={engine.id} engine={engine} />
          ))}
        </div>
      )}
    </div>
  );
}

function EngineCard({ engine }: { engine: EngineInfo }): JSX.Element {
  return (
    <div
      data-testid={`engine-card-${engine.id}`}
      className={`rounded-md border p-3 ${
        engine.available ? "border-line" : "border-dashed border-line opacity-70"
      }`}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] ${
              engine.available
                ? "bg-emerald-500/10 text-emerald-500"
                : "bg-amber-500/10 text-amber-500"
            }`}
          >
            {engine.available ? (
              <CheckCircle2 className="h-3 w-3" />
            ) : (
              <AlertTriangle className="h-3 w-3" />
            )}
            {engine.available ? "可用" : "不可用"}
          </span>
          <span className="text-sm font-medium text-fg">{engine.name}</span>
          <code className="text-[11px] text-ink-2">{engine.id}</code>
          {engine.version && (
            <span className="text-[11px] text-ink-2">v{engine.version}</span>
          )}
        </div>
      </div>
      {engine.unavailable_reason && (
        <p
          data-testid={`engine-reason-${engine.id}`}
          className="mt-2 text-xs text-status-error"
        >
          原因：{engine.unavailable_reason}
        </p>
      )}
      <div className="mt-2 flex flex-wrap gap-1.5">
        {Object.entries(engine.capabilities).map(([key, on]) => (
          <span
            key={key}
            className={`rounded px-1.5 py-0.5 text-[11px] ${
              on ? "bg-accent/10 text-accent" : "bg-line/40 text-ink-2 line-through"
            }`}
            title={on ? undefined : "该引擎不支持此能力，UI 已禁用"}
          >
            {CAPABILITY_LABELS[key] ?? key}
          </span>
        ))}
      </div>
    </div>
  );
}

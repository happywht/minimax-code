/**
 * Models tab — list and select LLM models via `model.*` IPC.
 */
import { useEffect } from "react";
import { Check } from "lucide-react";
import { useModelStore } from "../../stores";

export { ModelsTab };

function ModelsTab(): JSX.Element {
  const models = useModelStore((s) => s.models);
  const current = useModelStore((s) => s.current);
  const refresh = useModelStore((s) => s.refresh);
  const setCurrent = useModelStore((s) => s.setCurrent);
  const loading = useModelStore((s) => s.loading);

  useEffect(() => {
    if (models.length === 0) void refresh();
  }, [models.length, refresh]);

  return (
    <section data-testid="settings-models" className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium">Available models</h2>
        <button
          type="button" data-testid="settings-models-refresh"
          onClick={() => void refresh()}
          className="rounded border border-minimax-border bg-minimax-panel px-2 py-0.5 text-[11px] text-minimax-muted hover:text-minimax-fg"
        >
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>
      <ul className="space-y-1.5" data-testid="settings-models-list">
        {models.length === 0 && !loading && (
          <li className="rounded border border-dashed border-minimax-border px-3 py-4 text-center text-xs text-minimax-muted">
            No models available — add a provider first.
          </li>
        )}
        {models.map((m) => {
          const isCurrent = m.id === current;
          return (
            <li key={m.id} data-testid={`settings-model-${m.id}`}
              className={
                "flex items-center justify-between rounded-md border px-3 py-2 text-sm " +
                (isCurrent ? "border-minimax-accent/40 bg-minimax-accent/5" : "border-minimax-border bg-minimax-panel/40")
              }
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-minimax-fg">{m.name}</span>
                  {isCurrent && (
                    <span data-testid={`settings-model-current-${m.id}`}
                      className="rounded bg-minimax-accent/20 px-1.5 py-0.5 text-[11px] text-minimax-accent">current</span>
                  )}
                  {m.protocol && (
                    <span className="rounded bg-minimax-border px-1 py-0.5 text-[11px] font-mono text-minimax-muted">{m.protocol}</span>
                  )}
                </div>
                <div className="mt-0.5 text-[11px] text-minimax-muted">
                  {m.provider} · {(m.context_window / 1000).toFixed(0)}k ctx{m.supports_tools ? " · tools" : ""}
                </div>
              </div>
              <button type="button" data-testid={`settings-model-select-${m.id}`}
                onClick={() => { if (!isCurrent) void setCurrent(m.id); }}
                disabled={isCurrent}
                className={
                  "ml-3 inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs " +
                  (isCurrent ? "cursor-not-allowed border-minimax-border text-minimax-muted" : "border-minimax-accent/40 text-minimax-accent hover:bg-minimax-accent/10")
                }
              >
                {isCurrent ? <Check size={12} /> : null}
                {isCurrent ? "Selected" : "Use"}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

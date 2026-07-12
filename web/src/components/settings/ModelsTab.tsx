/**
 * Models tab — list and select LLM models via `model.*` IPC.
 */
import { useEffect, useState } from "react";
import { Check, Plus, Trash2 } from "lucide-react";
import { useModelStore, useProviderStore } from "../../stores";
import type { ProviderInfo, ProviderModel } from "../../types/ipc";
import { requestConfirmation } from "../ConfirmationDialog";

export { ModelsTab };

function ModelsTab(): JSX.Element {
  const models = useModelStore((s) => s.models);
  const current = useModelStore((s) => s.current);
  const refresh = useModelStore((s) => s.refresh);
  const setCurrent = useModelStore((s) => s.setCurrent);
  const loading = useModelStore((s) => s.loading);
  const providers = useProviderStore((s) => s.providers);
  const providersLoading = useProviderStore((s) => s.loading);
  const refreshProviders = useProviderStore((s) => s.refresh);
  const updateProvider = useProviderStore((s) => s.update);
  const [drafts, setDrafts] = useState<Record<string, { id: string; name: string; ctx: string }>>({});

  useEffect(() => {
    if (models.length === 0) void refresh();
  }, [models.length, refresh]);

  useEffect(() => {
    if (providers.length === 0) void refreshProviders();
  }, [providers.length, refreshProviders]);

  const setDraft = (providerId: string, patch: Partial<{ id: string; name: string; ctx: string }>) => {
    setDrafts((currentDrafts) => ({
      ...currentDrafts,
      [providerId]: {
        ...(currentDrafts[providerId] ?? { id: "", name: "", ctx: "128000" }),
        ...patch,
      },
    }));
  };

  const addModel = async (provider: ProviderInfo) => {
    const draft = drafts[provider.id] ?? { id: "", name: "", ctx: "128000" };
    const id = draft.id.trim();
    if (!id) return;
    const model: ProviderModel = {
      id,
      name: draft.name.trim() || id,
      context_window: Number.parseInt(draft.ctx, 10) || 128000,
      supports_tools: true,
    };
    const nextModels = [
      ...(provider.models ?? []).filter((m) => m.id !== id),
      model,
    ];
    const result = await updateProvider({ provider_id: provider.id, models: nextModels });
    if (result) {
      setDrafts((currentDrafts) => ({
        ...currentDrafts,
        [provider.id]: { id: "", name: "", ctx: "128000" },
      }));
      await refresh();
    }
  };

  const removeModel = async (provider: ProviderInfo, modelId: string) => {
    const accepted = await requestConfirmation({
      title: `Remove model ${modelId}?`,
      description: `This removes the model from ${provider.name}. Conversations that selected it will need another active model.`,
      confirmLabel: "Remove Model",
    });
    if (!accepted) return;
    const result = await updateProvider({
      provider_id: provider.id,
      models: (provider.models ?? []).filter((m) => m.id !== modelId),
    });
    if (result) await refresh();
  };

  return (
    <section data-testid="settings-models" className="min-w-0 space-y-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-medium">Available models</h2>
          <p className="mt-0.5 text-[11px] text-minimax-muted">
            Select the active model, then manage each provider's model registry below.
          </p>
        </div>
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
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <span className="max-w-full truncate font-medium text-minimax-fg">{m.name}</span>
                  {isCurrent && (
                    <span data-testid={`settings-model-current-${m.id}`}
                      className="rounded bg-minimax-accent/20 px-1.5 py-0.5 text-[11px] text-minimax-accent">current</span>
                  )}
                  {m.protocol && (
                    <span className="rounded bg-minimax-border px-1 py-0.5 text-[11px] font-mono text-minimax-muted">{m.protocol}</span>
                  )}
                </div>
                <div className="mt-0.5 truncate text-[11px] text-minimax-muted">
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

      <div data-testid="settings-model-registry" className="space-y-2 border-t border-minimax-border pt-4">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-medium text-minimax-fg">Provider model registry</h3>
          {providersLoading && (
            <span className="text-[11px] text-minimax-muted">Loading providers…</span>
          )}
        </div>
        {providers.length === 0 && !providersLoading && (
          <div className="rounded border border-dashed border-minimax-border px-3 py-3 text-center text-xs text-minimax-muted">
            No providers found. Add a provider before registering models.
          </div>
        )}
        {providers.map((provider) => {
          const draft = drafts[provider.id] ?? { id: "", name: "", ctx: "128000" };
          return (
            <div
              key={provider.id}
              data-testid={`settings-model-provider-${provider.id}`}
              className="rounded-md border border-minimax-border bg-minimax-panel/40 p-3"
            >
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-xs font-medium text-minimax-fg">{provider.name}</div>
                  <div className="truncate text-[11px] font-mono text-minimax-muted">{provider.base_url}</div>
                </div>
                <span className="shrink-0 rounded bg-minimax-border px-1.5 py-0.5 text-[11px] text-minimax-muted">
                  {(provider.models ?? []).length} models
                </span>
              </div>
              {(provider.models ?? []).length > 0 && (
                <ul className="mt-2 flex flex-wrap gap-1.5">
                  {(provider.models ?? []).map((model) => (
                    <li
                      key={model.id}
                      className="inline-flex min-w-0 items-center gap-1 rounded border border-minimax-border bg-minimax-bg px-1.5 py-1 text-[11px]"
                    >
                      <span className="max-w-[180px] truncate text-minimax-fg">{model.name || model.id}</span>
                      <span className="text-minimax-muted">{(model.context_window / 1000).toFixed(0)}k</span>
                      <button
                        type="button"
                        data-testid={`settings-model-remove-${provider.id}-${model.id}`}
                        aria-label={`Remove ${model.id}`}
                        onClick={() => void removeModel(provider, model.id)}
                        className="rounded p-0.5 text-minimax-muted transition-colors duration-200 hover:bg-minimax-border hover:text-status-error"
                      >
                        <Trash2 size={10} />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <div className="mt-2 grid grid-cols-1 gap-1.5 sm:grid-cols-12">
                <input
                  data-testid={`settings-model-add-id-${provider.id}`}
                  name={`model-id-${provider.id}`}
                  aria-label={`${provider.name} model ID`}
                  autoComplete="off"
                  spellCheck={false}
                  value={draft.id}
                  onChange={(e) => setDraft(provider.id, { id: e.target.value })}
                  placeholder="model id…"
                  className="min-w-0 rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-[11px] text-minimax-fg sm:col-span-4"
                />
                <input
                  data-testid={`settings-model-add-name-${provider.id}`}
                  name={`model-name-${provider.id}`}
                  aria-label={`${provider.name} model display name`}
                  autoComplete="off"
                  value={draft.name}
                  onChange={(e) => setDraft(provider.id, { name: e.target.value })}
                  placeholder="display name…"
                  className="min-w-0 rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-[11px] text-minimax-fg sm:col-span-4"
                />
                <input
                  data-testid={`settings-model-add-ctx-${provider.id}`}
                  name={`model-context-${provider.id}`}
                  aria-label={`${provider.name} context window`}
                  type="number"
                  inputMode="numeric"
                  min="1"
                  value={draft.ctx}
                  onChange={(e) => setDraft(provider.id, { ctx: e.target.value })}
                  placeholder="context…"
                  className="min-w-0 rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-[11px] text-minimax-fg sm:col-span-2"
                />
                <button
                  type="button"
                  data-testid={`settings-model-add-submit-${provider.id}`}
                  disabled={!draft.id.trim()}
                  onClick={() => void addModel(provider)}
                  className="inline-flex items-center justify-center gap-1 rounded border border-minimax-accent/40 bg-minimax-accent/10 px-2 py-1 text-[11px] text-minimax-accent transition-colors duration-200 hover:bg-minimax-accent/20 disabled:cursor-not-allowed disabled:opacity-50 sm:col-span-2"
                >
                  <Plus size={10} />
                  Add
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

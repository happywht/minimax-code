/**
 * Models tab — list and select LLM models via `model.*` IPC.
 *
 * Slim container: data + mutations live in
 * `models/useModelRegistry`, rows in `models/ModelRow` and
 * `models/ProviderRegistryCard`.
 */
import { RefreshCw } from "lucide-react";
import { Button, EmptyState, Spinner } from "../../ui";
import { TabHeader } from "./fields";
import { useModelRegistry } from "./models/useModelRegistry";
import { ModelRow } from "./models/ModelRow";
import { ProviderRegistryCard } from "./models/ProviderRegistryCard";

export { ModelsTab };

function ModelsTab(): JSX.Element {
  const {
    models,
    current,
    loading,
    providers,
    providersLoading,
    refresh,
    setCurrent,
    draftFor,
    setDraft,
    addModel,
    removeModel,
  } = useModelRegistry();

  return (
    <section data-testid="settings-models" className="min-w-0 space-y-5">
      <TabHeader
        title="Available models"
        hint="Select the active model, then manage each provider's model registry below."
        action={
          <Button
            size="sm"
            variant="secondary"
            data-testid="settings-models-refresh"
            onClick={() => void refresh()}
            loading={loading}
            icon={<RefreshCw />}
          >
            Refresh
          </Button>
        }
      />

      <ul className="space-y-1.5" data-testid="settings-models-list">
        {models.length === 0 && !loading && (
          <li>
            <EmptyState title="暂无可用模型 — 请先添加 Provider" />
          </li>
        )}
        {models.map((m) => (
          <ModelRow
            key={m.id}
            model={m}
            isCurrent={m.id === current}
            onSelect={(id) => void setCurrent(id)}
          />
        ))}
      </ul>

      <div data-testid="settings-model-registry" className="space-y-2 border-t border-line pt-4">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-1">
            Provider model registry
          </h3>
          {providersLoading && (
            <span className="flex items-center gap-1.5 text-[11px] text-ink-2">
              <Spinner size={11} /> Loading providers…
            </span>
          )}
        </div>
        {providers.length === 0 && !providersLoading && (
          <EmptyState title="暂无 Provider" hint="添加 Provider 后才能注册模型。" />
        )}
        {providers.map((provider) => (
          <ProviderRegistryCard
            key={provider.id}
            provider={provider}
            draft={draftFor(provider.id)}
            onDraftChange={(patch) => setDraft(provider.id, patch)}
            onAdd={() => void addModel(provider)}
            onRemove={(modelId) => void removeModel(provider, modelId)}
          />
        ))}
      </div>
    </section>
  );
}

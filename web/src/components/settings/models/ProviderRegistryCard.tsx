/**
 * ProviderRegistryCard — one provider's model registry inside the
 * Models tab: existing model chips plus the add-model draft row.
 */
import { Plus, Trash2 } from "lucide-react";
import { Badge, Button, IconButton, Input } from "../../../ui";
import type { ProviderInfo } from "../../../types/ipc";
import type { ModelDraft } from "./useModelRegistry";

export interface ProviderRegistryCardProps {
  provider: ProviderInfo;
  draft: ModelDraft;
  onDraftChange: (patch: Partial<ModelDraft>) => void;
  onAdd: () => void;
  onRemove: (modelId: string) => void;
}

export function ProviderRegistryCard({
  provider,
  draft,
  onDraftChange,
  onAdd,
  onRemove,
}: ProviderRegistryCardProps): JSX.Element {
  const models = provider.models ?? [];
  return (
    <div
      data-testid={`settings-model-provider-${provider.id}`}
      className="rounded-lg border border-line bg-surface-2 p-3"
    >
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-xs font-medium text-ink-0">{provider.name}</div>
          <div className="truncate font-mono text-[11px] text-ink-2">{provider.base_url}</div>
        </div>
        <Badge tone="neutral">{models.length} models</Badge>
      </div>

      {models.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {models.map((model) => (
            <li
              key={model.id}
              className="inline-flex min-w-0 items-center gap-1 rounded-md border border-line bg-surface-1 px-1.5 py-1 text-[11px]"
            >
              <span className="max-w-[180px] truncate text-ink-0">{model.name || model.id}</span>
              <span className="text-ink-2">{(model.context_window / 1000).toFixed(0)}k</span>
              <IconButton
                size="sm"
                data-testid={`settings-model-remove-${provider.id}-${model.id}`}
                aria-label={`Remove ${model.id}`}
                onClick={() => onRemove(model.id)}
              >
                <Trash2 />
              </IconButton>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-2 grid grid-cols-1 gap-1.5 sm:grid-cols-12">
        <Input
          data-testid={`settings-model-add-id-${provider.id}`}
          name={`model-id-${provider.id}`}
          aria-label={`${provider.name} model ID`}
          autoComplete="off"
          spellCheck={false}
          value={draft.id}
          onChange={(e) => onDraftChange({ id: e.target.value })}
          placeholder="model id…"
          className="sm:col-span-4"
        />
        <Input
          data-testid={`settings-model-add-name-${provider.id}`}
          name={`model-name-${provider.id}`}
          aria-label={`${provider.name} model display name`}
          autoComplete="off"
          value={draft.name}
          onChange={(e) => onDraftChange({ name: e.target.value })}
          placeholder="display name…"
          className="sm:col-span-4"
        />
        <Input
          data-testid={`settings-model-add-ctx-${provider.id}`}
          name={`model-context-${provider.id}`}
          aria-label={`${provider.name} context window`}
          type="number"
          inputMode="numeric"
          min="1"
          value={draft.ctx}
          onChange={(e) => onDraftChange({ ctx: e.target.value })}
          placeholder="context…"
          className="sm:col-span-2"
        />
        <Button
          size="sm"
          variant="subtle"
          data-testid={`settings-model-add-submit-${provider.id}`}
          disabled={!draft.id.trim()}
          onClick={onAdd}
          icon={<Plus />}
          className="sm:col-span-2"
        >
          Add
        </Button>
      </div>
    </div>
  );
}

/**
 * Providers tab — manage LLM providers via `provider.*` IPC.
 *
 * Slim container: form state lives in `providers/useProviderForm`,
 * presentation in `providers/ProviderForm` / `providers/ProviderCard`,
 * preset templates in `providers/presets`.
 */
import { useEffect } from "react";
import { Plus } from "lucide-react";
import { Button, EmptyState, Spinner } from "../../ui";
import { useProviderStore } from "../../stores";
import { toast } from "../layout/ErrorBoundary";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import type { ProviderInfo } from "../../types/ipc";
import { TabHeader } from "./fields";
import { useProviderForm } from "./providers/useProviderForm";
import { ProviderForm } from "./providers/ProviderForm";
import { ProviderCard } from "./providers/ProviderCard";

export { ProvidersTab };

function ProvidersTab(): JSX.Element {
  const providers = useProviderStore((s) => s.providers);
  const loading = useProviderStore((s) => s.loading);
  const refresh = useProviderStore((s) => s.refresh);
  const remove = useProviderStore((s) => s.remove);
  const setApiKey = useProviderStore((s) => s.setApiKey);
  const clearApiKey = useProviderStore((s) => s.clearApiKey);

  const form = useProviderForm();

  useEffect(() => {
    if (providers.length === 0) void refresh();
  }, [providers.length, refresh]);

  const handleDelete = async (p: ProviderInfo) => {
    if (p.id === "builtin-minimax") {
      toast.error("Cannot delete", "The built-in MiniMax provider cannot be removed.");
      return;
    }
    const accepted = await requestConfirmation({
      title: `Delete ${p.name}?`,
      description: "This removes the provider, its model registry, and its saved API key from MiniMax Code. This action cannot be undone.",
      confirmLabel: "Delete Provider",
    });
    if (!accepted) return;
    await remove(p.id);
    toast.success("Provider deleted", p.name);
  };

  const handleSetKey = async (providerId: string, key: string) => {
    const ok = await setApiKey(providerId, key);
    if (ok) toast.success("API key saved");
  };

  const handleClearKey = async (provider: ProviderInfo) => {
    const accepted = await requestConfirmation({
      title: `Clear ${provider.name} API key?`,
      description: "Real model requests through this provider will stop until you save another key. Existing conversations are not deleted.",
      confirmLabel: "Clear API Key",
    });
    if (!accepted) return;
    const ok = await clearApiKey(provider.id);
    if (ok) toast.success("API key cleared");
  };

  return (
    <section data-testid="settings-providers" className="min-w-0 space-y-4">
      <TabHeader
        title="LLM Providers"
        hint="Manage LLM providers and API keys. Models from enabled providers appear in the model selector."
        action={
          <Button
            size="sm"
            variant="subtle"
            data-testid="settings-provider-add"
            onClick={form.openForCreate}
            icon={<Plus />}
          >
            Add Provider
          </Button>
        }
      />

      {form.showForm && <ProviderForm form={form} />}

      {loading && providers.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-4 text-xs text-ink-2">
          <Spinner size={12} /> Loading providers…
        </div>
      ) : providers.length === 0 ? (
        <EmptyState
          title="暂无 Provider"
          hint='点击「添加 Provider」开始配置。'
        />
      ) : (
        <ul className="space-y-2" data-testid="settings-providers-list">
          {providers.map((p) => (
            <ProviderCard
              key={p.id}
              provider={p}
              onEdit={() => form.startEdit(p)}
              onDelete={() => void handleDelete(p)}
              onSetKey={(key) => void handleSetKey(p.id, key)}
              onClearKey={() => void handleClearKey(p)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

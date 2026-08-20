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
import { strings } from "../../ui/strings";
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
      toast.error(strings.settings.providers.builtinDeleteTitle, strings.settings.providers.builtinDeleteDesc);
      return;
    }
    const accepted = await requestConfirmation({
      title: strings.settings.providers.deleteTitle(p.name),
      description: strings.settings.providers.deleteDesc,
      confirmLabel: strings.settings.providers.deleteLabel,
    });
    if (!accepted) return;
    await remove(p.id);
    toast.success(strings.settings.providers.deletedToast, p.name);
  };

  const handleSetKey = async (providerId: string, key: string) => {
    const ok = await setApiKey(providerId, key);
    if (ok) toast.success(strings.settings.providers.keySavedToast);
  };

  const handleClearKey = async (provider: ProviderInfo) => {
    const accepted = await requestConfirmation({
      title: strings.settings.providers.clearKeyTitle(provider.name),
      description: strings.settings.providers.clearKeyDesc,
      confirmLabel: strings.settings.providers.clearKeyLabel,
    });
    if (!accepted) return;
    const ok = await clearApiKey(provider.id);
    if (ok) toast.success(strings.settings.providers.keyClearedToast);
  };

  return (
    <section data-testid="settings-providers" className="min-w-0 space-y-4">
      <TabHeader
        title={strings.settings.providers.title}
        hint={strings.settings.providers.hint}
        action={
          <Button
            size="sm"
            variant="subtle"
            data-testid="settings-provider-add"
            onClick={form.openForCreate}
            icon={<Plus />}
          >
            {strings.settings.providers.add}
          </Button>
        }
      />

      {form.showForm && <ProviderForm form={form} />}

      {loading && providers.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-4 text-xs text-ink-2">
          <Spinner size={12} /> {strings.settings.providers.loading}
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

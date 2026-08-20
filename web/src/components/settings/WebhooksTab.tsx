/**
 * Webhooks tab — manage inbound webhook endpoints.
 *
 * Slim container: create-form state lives in
 * `webhooks/useWebhookForm`, presentation in
 * `webhooks/WebhookCreateForm` / `webhooks/WebhookRow`.
 */
import { useEffect } from "react";
import { Plus, RefreshCw } from "lucide-react";
import { Button, EmptyState } from "../../ui";
import { strings } from "../../ui/strings";
import { SkeletonTable } from "../layout/Skeleton";
import { useWebhookStore } from "../../stores";
import type { WebhookConfig } from "../../types/ipc";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { ErrorBanner, TabHeader } from "./fields";
import { useWebhookForm } from "./webhooks/useWebhookForm";
import { WebhookCreateForm } from "./webhooks/WebhookCreateForm";
import { WebhookRow } from "./webhooks/WebhookRow";

export { WebhooksTab };

function WebhooksTab(): JSX.Element {
  const { entries, total, loading, error, refresh, remove, regenerateSecret, update } = useWebhookStore();
  const form = useWebhookForm();

  useEffect(() => { refresh(); }, []); // eslint-disable-line react-hooks/exhaustive-deps -- mount-only fetch

  const handleRegenerate = async (wh: WebhookConfig) => {
    const accepted = await requestConfirmation({
      title: strings.settings.webhooks.regenerateTitle(wh.name),
      description: strings.settings.webhooks.regenerateDesc,
      confirmLabel: strings.settings.webhooks.regenerateLabel,
    });
    if (accepted) await regenerateSecret(wh.id);
  };

  const handleDelete = async (wh: WebhookConfig) => {
    const accepted = await requestConfirmation({
      title: strings.settings.webhooks.deleteTitle(wh.name),
      description: strings.settings.webhooks.deleteDesc,
      confirmLabel: strings.settings.webhooks.deleteLabel,
    });
    if (accepted) await remove(wh.id);
  };

  return (
    <section data-testid="settings-webhooks-section" className="space-y-4">
      <TabHeader
        title={strings.settings.webhooks.title}
        hint={strings.settings.webhooks.hint}
        action={
          <>
            <Button size="sm" variant="secondary" onClick={() => refresh()} icon={<RefreshCw />}>
              {strings.settings.webhooks.refresh}
            </Button>
            <Button
              size="sm"
              variant="primary"
              data-testid="webhook-create-btn"
              onClick={form.toggleForm}
              icon={<Plus />}
            >
              {strings.settings.webhooks.new}
            </Button>
          </>
        }
      />

      {error && <ErrorBanner message={error} />}

      {form.showCreate && <WebhookCreateForm form={form} />}

      {loading ? (
        <SkeletonTable rows={3} />
      ) : entries.length === 0 ? (
        <EmptyState
          title="暂无 Webhook"
          hint='点击「新建」创建一个。'
        />
      ) : (
        <div className="space-y-2">
          {entries.map((wh) => (
            <WebhookRow
              key={wh.id}
              webhook={wh}
              onToggleEnabled={() => void update(wh.id, { enabled: !wh.enabled })}
              onRegenerateSecret={() => void handleRegenerate(wh)}
              onDelete={() => void handleDelete(wh)}
            />
          ))}
        </div>
      )}

      <div className="text-[11px] text-ink-2">{strings.settings.webhooks.footer(total)}</div>
    </section>
  );
}

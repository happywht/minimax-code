/**
 * WebhookRow — one configured webhook: status toggle, secret reveal,
 * re-secret, and delete actions.
 */
import { useState } from "react";
import { Eye, EyeOff, Trash2 } from "lucide-react";
import { Badge, Button, IconButton } from "../../../ui";
import { strings } from "../../../ui/strings";
import type { WebhookConfig } from "../../../types/ipc";

export interface WebhookRowProps {
  webhook: WebhookConfig;
  onToggleEnabled: () => void;
  onRegenerateSecret: () => void;
  onDelete: () => void;
}

export function WebhookRow({
  webhook,
  onToggleEnabled,
  onRegenerateSecret,
  onDelete,
}: WebhookRowProps): JSX.Element {
  const [revealed, setRevealed] = useState(false);

  return (
    <div className="space-y-2 rounded-lg border border-line bg-surface-2 p-3 transition-colors duration-150 hover:border-line-strong">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <span className="truncate text-xs font-semibold text-ink-0">{webhook.name}</span>
          <Badge tone="accent">{webhook.source}</Badge>
          <Badge tone="neutral">{webhook.action_type}</Badge>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button size="sm" variant="ghost" onClick={onToggleEnabled}>
            <span className={webhook.enabled ? "text-status-success" : "text-ink-2"}>
              {webhook.enabled
                ? strings.settings.webhooks.enabled
                : strings.settings.webhooks.disabled}
            </span>
          </Button>
          <Button size="sm" variant="ghost" onClick={onRegenerateSecret}>
            {strings.settings.webhooks.regenerateLabel}
          </Button>
          <IconButton
            aria-label={strings.settings.webhooks.deleteAria(webhook.name)}
            onClick={onDelete}
          >
            <Trash2 />
          </IconButton>
        </div>
      </div>
      <div className="font-mono text-[11px] text-ink-2">POST {webhook.url_path}</div>
      {webhook.secret && (
        <div className="flex items-center gap-1 text-[11px] text-ink-2">
          <span>{strings.settings.webhooks.secretLabel}</span>
          <span className="font-mono">{revealed ? webhook.secret : "••••••••"}</span>
          <IconButton
            size="sm"
            aria-label={
              revealed
                ? strings.settings.webhooks.hideSecretAria
                : strings.settings.webhooks.showSecretAria
            }
            onClick={() => setRevealed((v) => !v)}
          >
            {revealed ? <EyeOff /> : <Eye />}
          </IconButton>
        </div>
      )}
    </div>
  );
}

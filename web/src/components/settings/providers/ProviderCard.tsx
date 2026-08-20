/**
 * ProviderCard — one provider row in the Providers tab: header with
 * status badges, expandable detail (models + API key management).
 */
import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Eye,
  EyeOff,
  Globe,
  Pencil,
  Trash2,
} from "lucide-react";
import { Badge, Button, IconButton, Input } from "../../../ui";
import { strings } from "../../../ui/strings";
import type { ProviderInfo } from "../../../types/ipc";

export interface ProviderCardProps {
  provider: ProviderInfo;
  onEdit: () => void;
  onDelete: () => void;
  onSetKey: (key: string) => void;
  onClearKey: () => void;
}

export function ProviderCard({
  provider,
  onEdit,
  onDelete,
  onSetKey,
  onClearKey,
}: ProviderCardProps): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const [keyDraft, setKeyDraft] = useState("");
  const [reveal, setReveal] = useState(false);
  const isBuiltin = provider.id === "builtin-minimax";

  return (
    <li
      data-testid={`settings-provider-${provider.id}`}
      className="rounded-lg border border-line bg-surface-2 transition-colors duration-150 hover:border-line-strong"
    >
      {/* Header row */}
      <div className="flex min-w-0 items-start gap-2 px-3 py-2.5">
        <Globe size={14} className="mt-0.5 shrink-0 text-accent" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="max-w-full truncate text-xs font-medium text-ink-0">
              {provider.name}
            </span>
            <Badge tone={provider.protocol === "anthropic" ? "warning" : "info"} className="font-mono">
              {provider.protocol}
            </Badge>
            {!provider.enabled && (
              <Badge tone="neutral">{strings.settings.providers.card.disabled}</Badge>
            )}
            {provider.api_key_configured ? (
              <Badge tone="success" dot>
                {strings.settings.providers.card.keyOk}
              </Badge>
            ) : (
              <Badge tone="error" dot>
                {strings.settings.providers.card.noKey}
              </Badge>
            )}
          </div>
          <span className="block truncate font-mono text-[11px] text-ink-2">
            {provider.base_url}
          </span>
        </div>
        <IconButton
          onClick={() => setExpanded((v) => !v)}
          data-testid={`settings-provider-${provider.id}-expand`}
          aria-label={
            expanded
              ? strings.settings.providers.card.collapseAria(provider.name)
              : strings.settings.providers.card.expandAria(provider.name)
          }
          active={expanded}
        >
          {expanded ? <ChevronDown /> : <ChevronRight />}
        </IconButton>
        <IconButton onClick={onEdit} aria-label={strings.settings.providers.card.editAria}>
          <Pencil />
        </IconButton>
        {!isBuiltin && (
          <IconButton onClick={onDelete} aria-label={strings.settings.providers.card.deleteAria}>
            <Trash2 />
          </IconButton>
        )}
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="space-y-3 border-t border-line px-3 py-2.5">
          {/* Models */}
          {provider.models.length > 0 && (
            <div>
              <h4 className="mb-1 text-[11px] font-medium text-ink-2">
                {strings.settings.providers.card.modelsTitle(provider.models.length)}
              </h4>
              <div className="flex flex-wrap gap-1">
                {provider.models.map((m) => (
                  <span
                    key={m.id}
                    className="rounded-md border border-line bg-surface-1 px-1.5 py-0.5 text-[11px] text-ink-0"
                  >
                    {m.name || m.id}
                    <span className="ml-1 text-ink-2">
                      {(m.context_window / 1000).toFixed(0)}k
                    </span>
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* API key management */}
          <div>
            <h4 className="mb-1 text-[11px] font-medium text-ink-2">
              {strings.settings.providers.card.apiKeyTitle}
            </h4>
            <div className="flex min-w-0 flex-col gap-2 sm:flex-row">
              <div className="relative min-w-0 flex-1">
                <Input
                  id={`provider-${provider.id}-api-key`}
                  name={`provider-${provider.id}-api-key`}
                  aria-label={strings.settings.providers.card.keyAria(provider.name)}
                  type={reveal ? "text" : "password"}
                  data-testid={`settings-provider-${provider.id}-key-input`}
                  value={keyDraft}
                  onChange={(e) => setKeyDraft(e.target.value)}
                  placeholder={
                    provider.api_key_configured
                      ? strings.settings.providers.card.placeholderReplace
                      : strings.settings.providers.card.placeholderEnter
                  }
                  autoComplete="new-password"
                  spellCheck={false}
                  className="pr-8 font-mono"
                />
                <IconButton
                  size="sm"
                  onClick={() => setReveal((v) => !v)}
                  aria-label={
                    reveal
                      ? strings.settings.providers.card.hideKeyAria(provider.name)
                      : strings.settings.providers.card.showKeyAria(provider.name)
                  }
                  className="absolute right-1 top-1/2 -translate-y-1/2"
                >
                  {reveal ? <EyeOff /> : <Eye />}
                </IconButton>
              </div>
              <Button
                size="sm"
                variant="subtle"
                data-testid={`settings-provider-${provider.id}-key-save`}
                disabled={!keyDraft.trim()}
                onClick={() => { onSetKey(keyDraft.trim()); setKeyDraft(""); }}
              >
                {strings.settings.providers.card.save}
              </Button>
              {provider.api_key_configured && (
                <Button
                  size="sm"
                  variant="danger"
                  data-testid={`settings-provider-${provider.id}-key-clear`}
                  onClick={onClearKey}
                >
                  {strings.settings.providers.card.clear}
                </Button>
              )}
            </div>
          </div>
        </div>
      )}
    </li>
  );
}

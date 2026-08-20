/**
 * API Key tab — legacy MiniMax key management via `secrets.*` IPC.
 */
import { useEffect, useState } from "react";
import { Eye, EyeOff, KeyRound, Trash2 } from "lucide-react";
import { Badge, Button, IconButton, Input, Panel } from "../../ui";
import { strings } from "../../ui/strings";
import type { BadgeTone } from "../../ui";
import { useSecretStore } from "../../stores";
import { SkeletonLine } from "../layout/Skeleton";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { InlineCode, TabHeader } from "./fields";

export { ApiKeyTab };

const STATUS_META: Record<"keyring" | "env" | "none", { text: string; tone: BadgeTone }> = {
  keyring: { text: strings.settings.apiKey.statusKeyring, tone: "accent" },
  env: { text: strings.settings.apiKey.statusEnv, tone: "neutral" },
  none: { text: strings.settings.apiKey.statusNone, tone: "error" },
};

function ApiKeyTab(): JSX.Element {
  const status = useSecretStore((s) => s.status);
  const loading = useSecretStore((s) => s.loading);
  const refresh = useSecretStore((s) => s.refresh);
  const setKey = useSecretStore((s) => s.setKey);
  const clear = useSecretStore((s) => s.clear);

  const [draft, setDraft] = useState("");
  const [reveal, setReveal] = useState(false);

  useEffect(() => { if (status === null) void refresh(); }, [status, refresh]);

  const statusMeta = status ? STATUS_META[status.source] : null;
  const hasKey = status?.configured ?? false;

  const saveDraft = async () => {
    const ok = await setKey(draft);
    if (ok) { setDraft(""); setReveal(false); }
  };

  return (
    <section data-testid="settings-api-key" className="space-y-4">
      <TabHeader
        title={strings.settings.apiKey.title}
        hint={
          <>
            {strings.settings.apiKey.hintLead}
            <InlineCode>MINIMAX_API_KEY</InlineCode>
            {strings.settings.apiKey.hintTail}
          </>
        }
      />

      <div data-testid="settings-api-key-status" className="flex items-center gap-1.5">
        <Badge tone={statusMeta?.tone ?? "neutral"} className="gap-1.5 px-2.5 py-1 text-xs">
          <KeyRound size={12} aria-hidden="true" />
          {statusMeta ? (
            <span data-testid="settings-api-key-status-text">{statusMeta.text}</span>
          ) : (
            <SkeletonLine className="h-3 w-24" />
          )}
        </Badge>
      </div>

      <Panel title={hasKey ? strings.settings.apiKey.panelReplace : strings.settings.apiKey.panelNew}>
        <label htmlFor="api-key-input" className="sr-only">
          {strings.settings.apiKey.inputLabel}
        </label>
        <div className="flex gap-2">
          <div className="relative min-w-0 flex-1">
            <Input
              id="api-key-input"
              name="legacy-minimax-api-key"
              data-testid="settings-api-key-input"
              type={reveal ? "text" : "password"}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && draft.trim() && !loading) void saveDraft();
              }}
              placeholder="sk-…"
              autoComplete="new-password"
              spellCheck={false}
              className="pr-8 font-mono"
            />
            <IconButton
              size="sm"
              data-testid="settings-api-key-reveal"
              onClick={() => setReveal((v) => !v)}
              aria-label={reveal ? strings.settings.apiKey.hide : strings.settings.apiKey.reveal}
              className="absolute right-1 top-1/2 -translate-y-1/2"
            >
              {reveal ? <EyeOff /> : <Eye />}
            </IconButton>
          </div>
          <Button
            size="sm"
            variant="primary"
            data-testid="settings-api-key-save"
            disabled={!draft.trim() || loading}
            loading={loading}
            onClick={() => void saveDraft()}
          >
            {strings.settings.apiKey.save}
          </Button>
        </div>
        <p className="mt-1.5 text-[11px] text-ink-2">
          {strings.settings.apiKey.writeNote}
        </p>
      </Panel>

      {status?.source === "keyring" && (
        <Panel
          title={strings.settings.apiKey.keyringEntry}
          actions={
            <Button
              size="sm"
              variant="danger"
              data-testid="settings-api-key-clear"
              icon={<Trash2 />}
              disabled={loading}
              onClick={async () => {
                const accepted = await requestConfirmation({
                  title: strings.settings.apiKey.clearConfirmTitle,
                  description: strings.settings.apiKey.clearConfirmDesc,
                  confirmLabel: strings.settings.apiKey.clearConfirmLabel,
                });
                if (accepted) await clear();
              }}
            >
              {strings.settings.apiKey.clearButton}
            </Button>
          }
        >
          <p className="text-[11px] text-ink-2">
            {strings.settings.apiKey.clearNoteLead}{" "}
            <InlineCode>MINIMAX_API_KEY</InlineCode>
            {strings.settings.apiKey.clearNoteTail}
          </p>
        </Panel>
      )}
    </section>
  );
}

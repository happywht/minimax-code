/**
 * API Key tab — legacy MiniMax key management via `secrets.*` IPC.
 */
import { useEffect, useState } from "react";
import { Eye, EyeOff, KeyRound, Trash2 } from "lucide-react";
import { Badge, Button, IconButton, Input, Panel } from "../../ui";
import type { BadgeTone } from "../../ui";
import { useSecretStore } from "../../stores";
import { SkeletonLine } from "../layout/Skeleton";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { InlineCode, TabHeader } from "./fields";

export { ApiKeyTab };

const STATUS_META: Record<"keyring" | "env" | "none", { text: string; tone: BadgeTone }> = {
  keyring: { text: "Stored in OS keyring", tone: "accent" },
  env: { text: "Using environment variable", tone: "neutral" },
  none: { text: "Not configured — agent in mock mode", tone: "error" },
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
        title="MiniMax API key"
        hint={
          <>
            Legacy key for the built-in MiniMax provider. For multi-provider setups, use the
            Providers tab. Stored in the OS keyring (Windows Credential Manager / macOS Keychain /
            Linux Secret Service). Falls back to the
            <InlineCode>MINIMAX_API_KEY</InlineCode>
            env var if no keyring entry exists.
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

      <Panel title={hasKey ? "Replace the keyring entry" : "Paste a key to store in the OS keyring"}>
        <label htmlFor="api-key-input" className="sr-only">
          MiniMax API key
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
              aria-label={reveal ? "Hide API key" : "Show API key"}
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
            Save
          </Button>
        </div>
        <p className="mt-1.5 text-[11px] text-ink-2">
          The key is written to the OS keyring on save. It is never echoed back through the wire
          after the write.
        </p>
      </Panel>

      {status?.source === "keyring" && (
        <Panel
          title="Keyring entry"
          actions={
            <Button
              size="sm"
              variant="danger"
              data-testid="settings-api-key-clear"
              icon={<Trash2 />}
              disabled={loading}
              onClick={async () => {
                const accepted = await requestConfirmation({
                  title: "Clear the legacy MiniMax API key?",
                  description: "MiniMax requests using the legacy key will stop until you save another key. Existing conversations are not deleted.",
                  confirmLabel: "Clear API Key",
                });
                if (accepted) await clear();
              }}
            >
              Clear keyring
            </Button>
          }
        >
          <p className="text-[11px] text-ink-2">
            Removes the entry from the OS keyring. Does not affect the{" "}
            <InlineCode>MINIMAX_API_KEY</InlineCode> env var.
          </p>
        </Panel>
      )}
    </section>
  );
}

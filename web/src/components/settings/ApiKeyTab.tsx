/**
 * API Key tab — legacy MiniMax key management via `secrets.*` IPC.
 */
import { useEffect, useState } from "react";
import { Eye, EyeOff, KeyRound, Save, Trash2 } from "lucide-react";
import { useSecretStore } from "../../stores";
import { SkeletonLine } from "../Skeleton";

export { ApiKeyTab };

function ApiKeyTab(): JSX.Element {
  const status = useSecretStore((s) => s.status);
  const loading = useSecretStore((s) => s.loading);
  const refresh = useSecretStore((s) => s.refresh);
  const setKey = useSecretStore((s) => s.setKey);
  const clear = useSecretStore((s) => s.clear);

  const [draft, setDraft] = useState("");
  const [reveal, setReveal] = useState(false);

  useEffect(() => { if (status === null) void refresh(); }, [status, refresh]);

  const sourceLabel: Record<"keyring" | "env" | "none", string> = {
    keyring: "OS keyring", env: "environment variable", none: "not configured",
  };

  const statusPill = status
    ? { keyring: { text: "Stored in OS keyring", tone: "bg-minimax-accent/20 text-minimax-accent", loading: false },
        env: { text: "Using environment variable", tone: "bg-minimax-border text-minimax-muted", loading: false },
        none: { text: "Not configured — agent in mock mode", tone: "bg-red-500/15 text-red-300", loading: false },
      }[status.source]
    : { text: "", tone: "", loading: true as const };

  const hasKey = status?.configured ?? false;

  return (
    <section data-testid="settings-api-key" className="space-y-4">
      <div>
        <h2 className="text-sm font-medium">MiniMax API key</h2>
        <p className="mt-0.5 text-[11px] text-minimax-muted">
          Legacy key for the built-in MiniMax provider. For multi-provider setups, use the Providers tab.
          Stored in the OS keyring (Windows Credential Manager / macOS Keychain / Linux Secret Service).
          Falls back to the
          <code className="mx-1 rounded bg-minimax-panel px-1.5 py-0.5 font-mono text-[11px]">MINIMAX_API_KEY</code>
          env var if no keyring entry exists.
        </p>
      </div>

      <div data-testid="settings-api-key-status"
        className={"inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs " + (statusPill.loading ? "bg-minimax-border" : statusPill.tone)}>
        <KeyRound size={12} />
        {statusPill.loading ? (
          <SkeletonLine className="h-3 w-24" />
        ) : (
          <span data-testid="settings-api-key-status-text">{statusPill.text}</span>
        )}
      </div>

      <div className="rounded-md border border-minimax-border bg-minimax-panel/40 p-3">
        <label htmlFor="api-key-input" className="text-[11px] text-minimax-muted">
          {hasKey ? "Replace the keyring entry" : "Paste a key to store in the OS keyring"}
        </label>
        <div className="mt-1.5 flex gap-2">
          <div className="relative flex-1">
            <input id="api-key-input" data-testid="settings-api-key-input"
              type={reveal ? "text" : "password"} value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && draft.trim() && !loading) {
                  void (async () => { const ok = await setKey(draft); if (ok) setDraft(""); })();
                }
              }}
              placeholder="sk-..." autoComplete="off" spellCheck={false}
              className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 pr-9 font-mono text-xs text-minimax-fg" />
            <button type="button" data-testid="settings-api-key-reveal"
              onClick={() => setReveal((v) => !v)}
              aria-label={reveal ? "Hide API key" : "Show API key"}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-minimax-muted hover:text-minimax-fg">
              {reveal ? <EyeOff size={12} /> : <Eye size={12} />}
            </button>
          </div>
          <button type="button" data-testid="settings-api-key-save"
            disabled={!draft.trim() || loading}
            onClick={async () => { const ok = await setKey(draft); if (ok) { setDraft(""); setReveal(false); } }}
            className="inline-flex items-center justify-center gap-1 rounded border border-minimax-accent/40 bg-minimax-accent/10 px-3 py-1 text-xs text-minimax-accent hover:bg-minimax-accent/20 disabled:cursor-not-allowed disabled:opacity-50">
            <Save size={12} /> {loading ? "Saving…" : "Save"}
          </button>
        </div>
        <p className="mt-1.5 text-[11px] text-minimax-muted">
          The key is written to <code>{sourceLabel.keyring}</code> on save.
          It is never echoed back through the wire after the write.
        </p>
      </div>

      {status?.source === "keyring" && (
        <div className="rounded-md border border-minimax-border bg-minimax-panel/40 p-3">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-xs font-medium">Keyring entry</h3>
              <p className="mt-0.5 text-[11px] text-minimax-muted">
                Removes the entry from the OS keyring. Does not affect the <code>MINIMAX_API_KEY</code> env var.
              </p>
            </div>
            <button type="button" data-testid="settings-api-key-clear"
              onClick={() => void clear()} disabled={loading}
              className="inline-flex items-center gap-1 rounded border border-minimax-border px-2 py-1 text-xs text-minimax-muted hover:text-red-300 disabled:cursor-not-allowed disabled:opacity-50">
              <Trash2 size={12} /> Clear keyring
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

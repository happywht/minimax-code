/**
 * Webhooks tab — manage inbound webhook endpoints.
 */
import { useEffect, useState } from "react";
import { SkeletonTable } from "../Skeleton";
import { Eye, EyeOff, Plus, Trash2 } from "lucide-react";
import { useWebhookStore } from "../../stores";
import type { WebhookConfig } from "../../types/ipc";
import { required, minLength, compose } from "../../lib/validators";
import { requestConfirmation } from "../ConfirmationDialog";

export { WebhooksTab };

function WebhooksTab(): JSX.Element {
  const { entries, total, loading, error, refresh, create, remove, regenerateSecret, update } = useWebhookStore();
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newSource, setNewSource] = useState<"github" | "gitee" | "custom">("github");
  const [newAction, setNewAction] = useState<"code-review" | "send-message">("send-message");
  const [revealedSecrets, setRevealedSecrets] = useState<Set<string>>(new Set());
  const [createError, setCreateError] = useState("");

  useEffect(() => { refresh(); }, []); // eslint-disable-line react-hooks/exhaustive-deps -- mount-only fetch

  const nameValidator = compose(required("Name"), minLength(2, "Name"));

  const handleCreate = async () => {
    const err = nameValidator(newName);
    if (err) { setCreateError(err); return; }
    setCreateError("");
    await create({ name: newName.trim(), source: newSource, action_type: newAction });
    setNewName("");
    setShowCreate(false);
  };

  const toggleSecret = (id: string) => {
    setRevealedSecrets((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <section data-testid="settings-webhooks-section" className="space-y-4">
      <div className="flex flex-col items-start gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">Webhooks</h2>
          <p className="text-[11px] text-minimax-muted">
            Configure inbound webhook endpoints for GitHub / Gitee push events and custom integrations.
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <button
            type="button"
            className="rounded border border-minimax-border px-2 py-1 text-xs hover:bg-minimax-accent/20"
            onClick={() => refresh()}
          >
            Refresh
          </button>
          <button
            type="button"
            data-testid="webhook-create-btn"
            className="flex items-center gap-1 rounded bg-minimax-accent px-2 py-1 text-xs text-white hover:bg-minimax-accent/80"
            onClick={() => setShowCreate(!showCreate)}
          >
            <Plus size={12} /> New
          </button>
        </div>
      </div>

      {error && <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-status-error">{error}</div>}

      {/* Create form */}
      {showCreate && (
        <div className="space-y-2 rounded border border-minimax-border bg-minimax-panel p-3">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-12">
            <div className="min-w-0 sm:col-span-5">
              <label htmlFor="webhook-name" className="mb-0.5 block text-[11px] text-minimax-muted">Name</label>
              <input id="webhook-name" name="webhook-name" autoComplete="off"
                data-testid="webhook-name-input"
                className={`w-full rounded border bg-minimax-bg px-2 py-1 text-xs ${createError ? "border-red-400" : "border-minimax-border"}`}
                placeholder="e.g. GitHub push…"
                value={newName}
                onChange={(e) => { setNewName(e.target.value); setCreateError(""); }}
                onKeyDown={(e) => { if (e.key === "Enter") handleCreate(); }}
              />
            </div>
            <div className="min-w-0 sm:col-span-3">
              <label htmlFor="webhook-source" className="mb-0.5 block text-[11px] text-minimax-muted">Source</label>
              <select id="webhook-source" name="webhook-source"
                className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs"
                value={newSource}
                onChange={(e) => setNewSource(e.target.value as "github" | "gitee" | "custom")}
              >
              <option value="github">GitHub</option>
              <option value="gitee">Gitee</option>
              <option value="custom">Custom</option>
              </select>
            </div>
            <div className="min-w-0 sm:col-span-4">
              <label htmlFor="webhook-action" className="mb-0.5 block text-[11px] text-minimax-muted">Action</label>
              <select id="webhook-action" name="webhook-action"
                className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs"
                value={newAction}
                onChange={(e) => setNewAction(e.target.value as "code-review" | "send-message")}
              >
              <option value="send-message">Send Message</option>
              <option value="code-review">Code Review</option>
              </select>
            </div>
          </div>
          {createError && (
            <p data-testid="webhook-create-error" aria-live="polite" className="text-[11px] text-status-error">{createError}</p>
          )}
          <div className="flex justify-end gap-2">
            <button type="button" className="text-xs text-minimax-muted" onClick={() => setShowCreate(false)}>Cancel</button>
            <button
              type="button"
              data-testid="webhook-create-submit"
              className="rounded bg-minimax-accent px-3 py-1 text-xs text-white hover:bg-minimax-accent/80"
              onClick={handleCreate}
            >
              Create
            </button>
          </div>
        </div>
      )}

      {/* List */}
      {loading ? (
        <SkeletonTable rows={3} />
      ) : entries.length === 0 ? (
        <div className="py-8 text-center text-xs text-minimax-muted">
          No webhooks configured. Click "New" to create one.
        </div>
      ) : (
        <div className="space-y-2">
          {entries.map((wh: WebhookConfig) => (
            <div key={wh.id} className="rounded border border-minimax-border bg-minimax-panel p-3 space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold">{wh.name}</span>
                  <span className="rounded bg-minimax-accent/20 px-1.5 py-0.5 text-[11px] text-minimax-accent">{wh.source}</span>
                  <span className="rounded bg-minimax-bg px-1.5 py-0.5 text-[11px] text-minimax-muted">{wh.action_type}</span>
                </div>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    className={`rounded px-1.5 py-0.5 text-[11px] ${wh.enabled ? "text-status-success" : "text-minimax-muted"}`}
                    onClick={() => update(wh.id, { enabled: !wh.enabled })}
                  >
                    {wh.enabled ? "Enabled" : "Disabled"}
                  </button>
                  <button
                    type="button"
                    className="rounded px-1.5 py-0.5 text-[11px] text-minimax-muted hover:text-minimax-accent"
                    onClick={async () => {
                      const accepted = await requestConfirmation({
                        title: `Regenerate secret for ${wh.name}?`,
                        description: "The current webhook secret will stop working immediately. Update the sender with the new secret before sending more events.",
                        confirmLabel: "Regenerate Secret",
                      });
                      if (accepted) await regenerateSecret(wh.id);
                    }}
                  >
                    Re-secret
                  </button>
                  <button
                    type="button"
                    aria-label={`Delete webhook ${wh.name}`}
                    className="rounded px-1.5 py-0.5 text-[11px] text-status-error hover:text-status-error"
                    onClick={async () => {
                      const accepted = await requestConfirmation({
                        title: `Delete webhook ${wh.name}?`,
                        description: "Inbound events sent to this webhook will no longer trigger MiniMax Code.",
                        confirmLabel: "Delete Webhook",
                      });
                      if (accepted) await remove(wh.id);
                    }}
                  >
                    <Trash2 size={11} aria-hidden="true" />
                  </button>
                </div>
              </div>
              <div className="text-[11px] text-minimax-muted font-mono">
                POST {wh.url_path}
              </div>
              {wh.secret && (
                <div className="flex items-center gap-1 text-[11px] text-minimax-muted">
                  <span>Secret:</span>
                  <span className="font-mono">{revealedSecrets.has(wh.id) ? wh.secret : "••••••••"}</span>
                  <button
                    type="button"
                    className="text-minimax-muted hover:text-minimax-fg"
                    onClick={() => toggleSecret(wh.id)}
                  >
                    {revealedSecrets.has(wh.id) ? <EyeOff size={10} /> : <Eye size={10} />}
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="text-[11px] text-minimax-muted">
        {total} webhook(s) configured
      </div>
    </section>
  );
}

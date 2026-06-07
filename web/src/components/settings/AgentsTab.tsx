/**
 * Agents tab — sub-agent CRUD via `agent.*` IPC.
 */
import { useEffect, useState } from "react";
import { Bot, Plus, Trash2 } from "lucide-react";
import { useAgentStore } from "../../stores";

export { AgentsTab };

function AgentsTab(): JSX.Element {
  const agents = useAgentStore((s) => s.agents);
  const loading = useAgentStore((s) => s.loading);
  const refresh = useAgentStore((s) => s.refresh);
  const create = useAgentStore((s) => s.create);
  const remove = useAgentStore((s) => s.remove);

  const [showForm, setShowForm] = useState(false);
  const [formName, setFormName] = useState("");
  const [formPrompt, setFormPrompt] = useState("");

  useEffect(() => { if (agents.length === 0) void refresh(); }, [agents.length, refresh]);

  const handleCreate = async () => {
    if (!formName.trim() || !formPrompt.trim()) return;
    const a = await create({ name: formName.trim(), system_prompt: formPrompt.trim() });
    if (a) { setFormName(""); setFormPrompt(""); setShowForm(false); }
  };

  return (
    <section data-testid="settings-agents" className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-medium">Sub-agents</h2>
          <p className="mt-0.5 text-[11px] text-minimax-muted">
            Manage agents that can be invoked via <code className="rounded bg-minimax-panel px-1 font-mono text-[10px]">@agent</code> in chat.
          </p>
        </div>
        <button type="button" data-testid="settings-agent-create"
          onClick={() => setShowForm((v) => !v)}
          className="inline-flex items-center gap-1 rounded border border-minimax-accent/40 bg-minimax-accent/10 px-2 py-1 text-xs text-minimax-accent hover:bg-minimax-accent/20">
          <Plus size={12} /> New Agent
        </button>
      </div>

      {showForm && (
        <div data-testid="settings-agent-form" className="rounded-md border border-minimax-border bg-minimax-panel/40 p-3 space-y-2">
          <input data-testid="settings-agent-form-name" value={formName}
            onChange={(e) => setFormName(e.target.value)} placeholder="Agent name (e.g. code-reviewer)"
            className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs text-minimax-fg" />
          <textarea data-testid="settings-agent-form-prompt" value={formPrompt}
            onChange={(e) => setFormPrompt(e.target.value)} placeholder="System prompt…" rows={3}
            className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs text-minimax-fg resize-none" />
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setShowForm(false)}
              className="rounded border border-minimax-border px-2 py-1 text-xs text-minimax-muted hover:text-minimax-fg">Cancel</button>
            <button type="button" data-testid="settings-agent-form-submit"
              onClick={() => void handleCreate()} disabled={!formName.trim() || !formPrompt.trim()}
              className="rounded border border-minimax-accent/40 bg-minimax-accent/10 px-2 py-1 text-xs text-minimax-accent hover:bg-minimax-accent/20 disabled:opacity-50 disabled:cursor-not-allowed">Create</button>
          </div>
        </div>
      )}

      {loading && agents.length === 0 ? (
        <div className="py-4 text-center text-xs text-minimax-muted">Loading agents…</div>
      ) : agents.length === 0 ? (
        <div className="py-4 text-center text-xs italic text-minimax-muted">No sub-agents configured. Click "New Agent" to create one.</div>
      ) : (
        <ul className="space-y-2">
          {agents.map((a) => (
            <li key={a.id} data-testid={`settings-agent-row-${a.name}`}
              className="flex items-center justify-between rounded-md border border-minimax-border bg-minimax-panel/40 p-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <Bot size={12} className="text-minimax-accent" />
                  <span className="truncate text-xs font-medium text-minimax-fg">{a.name}</span>
                  {a.enabled ? (
                    <span className="rounded bg-emerald-500/10 px-1 py-0.5 text-[9px] text-emerald-300">enabled</span>
                  ) : (
                    <span className="rounded bg-minimax-border px-1 py-0.5 text-[9px] text-minimax-muted">disabled</span>
                  )}
                </div>
                {a.description && <p className="mt-0.5 truncate text-[10px] text-minimax-muted">{a.description}</p>}
              </div>
              <button type="button" data-testid={`settings-agent-delete-${a.name}`}
                onClick={() => void remove(a.name)} aria-label={`Delete agent ${a.name}`}
                className="ml-2 rounded border border-minimax-border p-1 text-minimax-muted hover:text-red-300">
                <Trash2 size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

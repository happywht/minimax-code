/**
 * Teams tab — agent team management (v0.8.0).
 * Includes TEAM_COLORS preset array.
 */
import { useEffect, useState } from "react";
import { Check, Plus, Save, Trash2, Users } from "lucide-react";
import { useAgentStore, useTeamStore } from "../../stores";
import { toast } from "../ErrorBoundary";
import type { OrchestrationMode } from "../../types/ipc";

export { TeamsTab };

/** Color presets for team badges. */
const TEAM_COLORS = [
  "#6366f1", "#8b5cf6", "#ec4899", "#f43f5e",
  "#f97316", "#eab308", "#22c55e", "#14b8a6",
  "#06b6d4", "#3b82f6",
];

function TeamsTab(): JSX.Element {
  const teams = useTeamStore((s) => s.teams);
  const loading = useTeamStore((s) => s.loading);
  const refresh = useTeamStore((s) => s.refresh);
  const create = useTeamStore((s) => s.create);
  const remove = useTeamStore((s) => s.remove);
  const enable = useTeamStore((s) => s.enable);
  const disable = useTeamStore((s) => s.disable);

  // Available agents for team assignment
  const agents = useAgentStore((s) => s.agents);
  const refreshAgents = useAgentStore((s) => s.refresh);

  const [showForm, setShowForm] = useState(false);
  const [formName, setFormName] = useState("");
  const [formDescription, setFormDescription] = useState("");
  const [formMode, setFormMode] = useState<OrchestrationMode>("parallel");
  const [formColor, setFormColor] = useState(TEAM_COLORS[0]);
  const [formAgents, setFormAgents] = useState<string[]>([]);

  useEffect(() => {
    if (teams.length === 0) void refresh();
    if (agents.length === 0) void refreshAgents();
  }, [teams.length, refresh, agents.length, refreshAgents]);

  const resetForm = () => {
    setFormName(""); setFormDescription(""); setFormMode("parallel");
    setFormColor(TEAM_COLORS[0]); setFormAgents([]);
    setShowForm(false);
  };

  const handleCreate = async () => {
    if (!formName.trim()) return;
    const t = await create({
      name: formName.trim(),
      description: formDescription.trim(),
      color: formColor,
      agents: formAgents,
      orchestration_mode: formMode,
    });
    if (t) {
      toast.success("Team created", t.name);
      resetForm();
    }
  };

  const toggleAgent = (name: string) => {
    setFormAgents((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
    );
  };

  return (
    <section data-testid="settings-teams" className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-medium">Agent Teams</h2>
          <p className="mt-0.5 text-[11px] text-minimax-muted">
            Create named groups of agents that work together. Trigger with{" "}
            <code className="rounded bg-minimax-panel px-1 font-mono text-[11px]">@team:team-name</code> in chat.
          </p>
        </div>
        <button type="button" data-testid="settings-team-create"
          onClick={() => setShowForm((v) => !v)}
          className="inline-flex items-center gap-1 rounded border border-minimax-accent/40 bg-minimax-accent/10 px-2 py-1 text-xs text-minimax-accent hover:bg-minimax-accent/20">
          <Plus size={12} /> New Team
        </button>
      </div>

      {/* Create form */}
      {showForm && (
        <div data-testid="settings-team-form" className="rounded-md border border-minimax-border bg-minimax-panel/40 p-3 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-medium">New Team</h3>
            <button type="button" onClick={resetForm}
              className="text-[11px] text-minimax-muted hover:text-minimax-fg">Cancel</button>
          </div>

          <div className="grid grid-cols-12 gap-2">
            <div className="col-span-4">
              <label className="text-[11px] text-minimax-muted">Team Name</label>
              <input value={formName} onChange={(e) => setFormName(e.target.value)}
                placeholder="e.g. fullstack-review"
                className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs text-minimax-fg" />
            </div>
            <div className="col-span-4">
              <label className="text-[11px] text-minimax-muted">Orchestration</label>
              <select value={formMode} onChange={(e) => setFormMode(e.target.value as OrchestrationMode)}
                className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs text-minimax-fg">
                <option value="parallel">Parallel</option>
                <option value="sequential">Sequential</option>
                <option value="round-robin">Round-robin</option>
              </select>
            </div>
            <div className="col-span-4">
              <label className="text-[11px] text-minimax-muted">Color</label>
              <div className="flex flex-wrap gap-1 mt-0.5">
                {TEAM_COLORS.slice(0, 5).map((c) => (
                  <button key={c} type="button"
                    onClick={() => setFormColor(c)}
                    className={"h-5 w-5 rounded-full border-2 " + (formColor === c ? "border-white" : "border-transparent")}
                    style={{ backgroundColor: c }} />
                ))}
              </div>
            </div>
          </div>

          <div>
            <label className="text-[11px] text-minimax-muted">Description</label>
            <input value={formDescription} onChange={(e) => setFormDescription(e.target.value)}
              placeholder="What does this team do?"
              className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs text-minimax-fg" />
          </div>

          <div>
            <label className="text-[11px] text-minimax-muted">Agents ({formAgents.length} selected)</label>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {agents.length === 0 && (
                <span className="text-[11px] italic text-minimax-muted">No agents available — create some first.</span>
              )}
              {agents.map((a) => {
                const selected = formAgents.includes(a.name);
                return (
                  <button key={a.id} type="button"
                    onClick={() => toggleAgent(a.name)}
                    className={
                      "inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[11px] " +
                      (selected
                        ? "border-minimax-accent/40 bg-minimax-accent/10 text-minimax-accent"
                        : "border-minimax-border bg-minimax-bg text-minimax-muted hover:text-minimax-fg")
                    }>
                    {a.name}
                    {selected && <Check size={9} />}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="flex justify-end gap-2">
            <button type="button" onClick={resetForm}
              className="rounded border border-minimax-border px-2 py-1 text-xs text-minimax-muted hover:text-minimax-fg">Cancel</button>
            <button type="button" data-testid="settings-team-form-submit"
              onClick={() => void handleCreate()} disabled={!formName.trim()}
              className="rounded border border-minimax-accent/40 bg-minimax-accent/10 px-2 py-1 text-xs text-minimax-accent hover:bg-minimax-accent/20 disabled:opacity-50 disabled:cursor-not-allowed">
              <Save size={12} className="mr-1 inline" /> Create Team
            </button>
          </div>
        </div>
      )}

      {/* Team list */}
      {loading && teams.length === 0 ? (
        <div className="py-4 text-center text-xs text-minimax-muted">Loading teams…</div>
      ) : teams.length === 0 ? (
        <div className="py-4 text-center text-xs italic text-minimax-muted">
          No teams configured. Click "New Team" to create one.
        </div>
      ) : (
        <ul className="space-y-2">
          {teams.map((t) => (
            <li key={t.id} data-testid={`settings-team-row-${t.name}`}
              className="rounded-md border border-minimax-border bg-minimax-panel/40 p-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <span className="h-3 w-3 rounded-full shrink-0" style={{ backgroundColor: t.color || "#6366f1" }} />
                  <Users size={14} className="shrink-0 text-minimax-accent" />
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate text-xs font-medium text-minimax-fg">{t.name}</span>
                      <span className="rounded bg-minimax-accent/20 px-1 py-0.5 text-[11px] text-minimax-accent">
                        {t.orchestration_mode}
                      </span>
                      {t.enabled ? (
                        <span className="rounded bg-emerald-500/10 px-1 py-0.5 text-[11px] text-emerald-300">enabled</span>
                      ) : (
                        <span className="rounded bg-minimax-border px-1 py-0.5 text-[11px] text-minimax-muted">disabled</span>
                      )}
                    </div>
                    {t.description && <p className="mt-0.5 truncate text-[11px] text-minimax-muted">{t.description}</p>}
                    {t.agents.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {t.agents.map((a) => (
                          <span key={a} className="rounded bg-minimax-bg border border-minimax-border px-1.5 py-0.5 text-[11px] text-minimax-fg">
                            {a}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-1 ml-2">
                  <button type="button" data-testid={`settings-team-toggle-${t.name}`}
                    onClick={() => void (t.enabled ? disable(t.name) : enable(t.name))}
                    className="rounded border border-minimax-border px-1.5 py-0.5 text-[11px] text-minimax-muted hover:text-minimax-fg">
                    {t.enabled ? "Disable" : "Enable"}
                  </button>
                  <button type="button" data-testid={`settings-team-delete-${t.name}`}
                    onClick={() => void remove(t.name)} aria-label={`Delete team ${t.name}`}
                    className="rounded border border-minimax-border p-1 text-minimax-muted hover:text-status-error">
                    <Trash2 size={12} />
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

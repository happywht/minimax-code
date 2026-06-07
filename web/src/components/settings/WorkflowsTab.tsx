/**
 * Workflows tab — manage automation workflows.
 */
import { useEffect, useState } from "react";
import { SkeletonTable } from "../Skeleton";
import { Play, Plus, Trash2, Workflow } from "lucide-react";
import { useWorkflowStore } from "../../stores";
import type { WorkflowEntry } from "../../types/ipc";
import { formatDateTime } from "../../lib/time";

export { WorkflowsTab };

function WorkflowsTab(): JSX.Element {
  const { entries, total, loading, error, refresh, create, remove, enable, disable, trigger } = useWorkflowStore();
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newTriggerType, setNewTriggerType] = useState<"webhook" | "schedule" | "agent_event">("webhook");
  const [newDescription, setNewDescription] = useState("");

  useEffect(() => { refresh(); }, []); // eslint-disable-line react-hooks/exhaustive-deps -- mount-only fetch

  const handleCreate = async () => {
    if (!newName.trim()) return;
    await create({
      name: newName.trim(),
      trigger_type: newTriggerType,
      description: newDescription.trim(),
    });
    setNewName("");
    setNewDescription("");
    setShowCreate(false);
  };

  return (
    <section data-testid="settings-workflows-section" className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold">Workflows</h2>
          <p className="text-[11px] text-minimax-muted">
            Automation workflows triggered by webhooks, schedules, or agent events. Configure trigger conditions and action steps.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            className="rounded border border-minimax-border px-2 py-1 text-xs hover:bg-minimax-accent/20"
            onClick={() => refresh()}
          >
            Refresh
          </button>
          <button
            type="button"
            data-testid="workflow-create-btn"
            className="flex items-center gap-1 rounded bg-minimax-accent px-2 py-1 text-xs text-white hover:bg-minimax-accent/80"
            onClick={() => setShowCreate(!showCreate)}
          >
            <Plus size={12} /> New
          </button>
        </div>
      </div>

      {error && <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-400">{error}</div>}

      {/* Create form */}
      {showCreate && (
        <div className="space-y-2 rounded border border-minimax-border bg-minimax-panel p-3">
          <div className="flex items-center gap-2">
            <input
              data-testid="workflow-name-input"
              className="flex-1 rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs"
              placeholder="Workflow name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleCreate(); }}
            />
            <select
              className="rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs"
              value={newTriggerType}
              onChange={(e) => setNewTriggerType(e.target.value as "webhook" | "schedule" | "agent_event")}
            >
              <option value="webhook">Webhook</option>
              <option value="schedule">Schedule</option>
              <option value="agent_event">Agent Event</option>
            </select>
          </div>
          <input
            className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs"
            placeholder="Description (optional)"
            value={newDescription}
            onChange={(e) => setNewDescription(e.target.value)}
          />
          <div className="flex justify-end gap-2">
            <button type="button" className="text-xs text-minimax-muted" onClick={() => setShowCreate(false)}>Cancel</button>
            <button
              type="button"
              data-testid="workflow-create-submit"
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
          No workflows configured. Click "New" to create one.
        </div>
      ) : (
        <div className="space-y-2">
          {entries.map((wf: WorkflowEntry) => (
            <div key={wf.id} className="rounded border border-minimax-border bg-minimax-panel p-3 space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Workflow size={14} className="text-minimax-accent" />
                  <span className="text-xs font-semibold">{wf.name}</span>
                  <span className="rounded bg-minimax-accent/20 px-1.5 py-0.5 text-[11px] text-minimax-accent">{wf.trigger_type}</span>
                  <span className="rounded bg-minimax-bg px-1.5 py-0.5 text-[11px] text-minimax-muted">{wf.steps.length} step(s)</span>
                </div>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    className={`rounded px-1.5 py-0.5 text-[11px] ${wf.enabled ? "text-green-400" : "text-minimax-muted"}`}
                    onClick={() => wf.enabled ? disable(wf.id) : enable(wf.id)}
                  >
                    {wf.enabled ? "Enabled" : "Disabled"}
                  </button>
                  <button
                    type="button"
                    data-testid={`workflow-trigger-${wf.id}`}
                    className="rounded px-1.5 py-0.5 text-[11px] text-minimax-accent hover:text-minimax-accent/80"
                    onClick={() => trigger(wf.id)}
                    title="Manually trigger this workflow"
                  >
                    <Play size={11} />
                  </button>
                  <button
                    type="button"
                    className="rounded px-1.5 py-0.5 text-[11px] text-red-400 hover:text-red-300"
                    onClick={() => remove(wf.id)}
                  >
                    <Trash2 size={11} />
                  </button>
                </div>
              </div>
              {wf.description && (
                <p className="text-[11px] text-minimax-muted">{wf.description}</p>
              )}
              <div className="flex items-center gap-3 text-[11px] text-minimax-muted">
                <span>Runs: {wf.run_count}</span>
                {wf.last_run_at && <span>Last: {formatDateTime(wf.last_run_at)}</span>}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="text-[11px] text-minimax-muted">
        {total} workflow(s) configured
      </div>
    </section>
  );
}

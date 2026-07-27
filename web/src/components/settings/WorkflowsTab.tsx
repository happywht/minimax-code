/**
 * Workflows tab — manage automation workflows.
 */
import { useEffect, useState } from "react";
import { Play, Plus, RefreshCw, Trash2, Workflow } from "lucide-react";
import { Badge, Button, EmptyState, IconButton, Input, Panel } from "../../ui";
import { SkeletonTable } from "../layout/Skeleton";
import { useWorkflowStore } from "../../stores";
import type { WorkflowEntry } from "../../types/ipc";
import { formatDateTime } from "../../lib/time";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { ErrorBanner, Field, Select, TabHeader } from "./fields";

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
      <TabHeader
        title="Workflows"
        hint="Automation workflows triggered by webhooks, schedules, or agent events. Configure trigger conditions and action steps."
        action={
          <>
            <Button size="sm" variant="secondary" onClick={() => refresh()} icon={<RefreshCw />}>
              Refresh
            </Button>
            <Button
              size="sm"
              variant="primary"
              data-testid="workflow-create-btn"
              onClick={() => setShowCreate((v) => !v)}
              icon={<Plus />}
            >
              New
            </Button>
          </>
        }
      />

      {error && <ErrorBanner message={error} />}

      {/* Create form */}
      {showCreate && (
        <Panel
          title="New Workflow"
          actions={
            <Button size="sm" variant="ghost" onClick={() => setShowCreate(false)}>
              Cancel
            </Button>
          }
        >
          <div className="space-y-2">
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-12">
              <Field label="Name" htmlFor="workflow-name" className="sm:col-span-7">
                <Input
                  id="workflow-name"
                  name="workflow-name"
                  autoComplete="off"
                  data-testid="workflow-name-input"
                  placeholder="e.g. Pull request review…"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") void handleCreate(); }}
                />
              </Field>
              <Field label="Trigger" htmlFor="workflow-trigger-type" className="sm:col-span-5">
                <Select
                  id="workflow-trigger-type"
                  name="workflow-trigger-type"
                  value={newTriggerType}
                  onChange={(e) => setNewTriggerType(e.target.value as "webhook" | "schedule" | "agent_event")}
                >
                  <option value="webhook">Webhook</option>
                  <option value="schedule">Schedule</option>
                  <option value="agent_event">Agent Event</option>
                </Select>
              </Field>
            </div>
            <Field label="Description" htmlFor="workflow-description">
              <Input
                id="workflow-description"
                name="workflow-description"
                autoComplete="off"
                placeholder="e.g. Review incoming pull requests…"
                value={newDescription}
                onChange={(e) => setNewDescription(e.target.value)}
              />
            </Field>
            <div className="flex justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={() => setShowCreate(false)}>
                Cancel
              </Button>
              <Button
                size="sm"
                variant="primary"
                data-testid="workflow-create-submit"
                onClick={() => void handleCreate()}
              >
                Create
              </Button>
            </div>
          </div>
        </Panel>
      )}

      {/* List */}
      {loading ? (
        <SkeletonTable rows={3} />
      ) : entries.length === 0 ? (
        <EmptyState
          title="暂无工作流"
          hint='点击「新建」创建一个。'
        />
      ) : (
        <div className="space-y-2">
          {entries.map((wf: WorkflowEntry) => (
            <div
              key={wf.id}
              className="space-y-2 rounded-lg border border-line bg-surface-2 p-3 transition-colors duration-150 hover:border-line-strong"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                  <Workflow size={14} className="shrink-0 text-accent" />
                  <span className="truncate text-xs font-semibold text-ink-0">{wf.name}</span>
                  <Badge tone="accent">{wf.trigger_type}</Badge>
                  <Badge tone="neutral">{wf.steps.length} step(s)</Badge>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => void (wf.enabled ? disable(wf.id) : enable(wf.id))}
                  >
                    <span className={wf.enabled ? "text-status-success" : "text-ink-2"}>
                      {wf.enabled ? "Enabled" : "Disabled"}
                    </span>
                  </Button>
                  <IconButton
                    data-testid={`workflow-trigger-${wf.id}`}
                    aria-label={`Run workflow ${wf.name}`}
                    title="Manually trigger this workflow"
                    onClick={() => void trigger(wf.id)}
                  >
                    <Play />
                  </IconButton>
                  <IconButton
                    aria-label={`Delete workflow ${wf.name}`}
                    onClick={async () => {
                      const accepted = await requestConfirmation({
                        title: `Delete workflow ${wf.name}?`,
                        description: "The workflow definition and its trigger configuration will be permanently removed.",
                        confirmLabel: "Delete Workflow",
                      });
                      if (accepted) await remove(wf.id);
                    }}
                  >
                    <Trash2 />
                  </IconButton>
                </div>
              </div>
              {wf.description && (
                <p className="text-[11px] text-ink-2">{wf.description}</p>
              )}
              <div className="flex items-center gap-3 text-[11px] text-ink-2">
                <span>Runs: {wf.run_count}</span>
                {wf.last_run_at && <span>Last: {formatDateTime(wf.last_run_at)}</span>}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="text-[11px] text-ink-2">{total} workflow(s) configured</div>
    </section>
  );
}

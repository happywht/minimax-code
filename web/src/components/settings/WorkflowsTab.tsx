/**
 * Workflows tab — manage automation workflows.
 */
import { useEffect, useState } from "react";
import { Play, Plus, RefreshCw, Trash2, Workflow } from "lucide-react";
import { Badge, Button, EmptyState, IconButton, Input, Panel } from "../../ui";
import { strings } from "../../ui/strings";
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
        title={strings.settings.workflows.title}
        hint={strings.settings.workflows.hint}
        action={
          <>
            <Button size="sm" variant="secondary" onClick={() => refresh()} icon={<RefreshCw />}>
              {strings.settings.workflows.refresh}
            </Button>
            <Button
              size="sm"
              variant="primary"
              data-testid="workflow-create-btn"
              onClick={() => setShowCreate((v) => !v)}
              icon={<Plus />}
            >
              {strings.settings.workflows.new}
            </Button>
          </>
        }
      />

      {error && <ErrorBanner message={error} />}

      {/* Create form */}
      {showCreate && (
        <Panel
          title={strings.settings.workflows.createTitle}
          actions={
            <Button size="sm" variant="ghost" onClick={() => setShowCreate(false)}>
              {strings.settings.workflows.cancel}
            </Button>
          }
        >
          <div className="space-y-2">
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-12">
              <Field label={strings.settings.workflows.fieldName} htmlFor="workflow-name" className="sm:col-span-7">
                <Input
                  id="workflow-name"
                  name="workflow-name"
                  autoComplete="off"
                  data-testid="workflow-name-input"
                  placeholder={strings.settings.workflows.placeholderName}
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") void handleCreate(); }}
                />
              </Field>
              <Field label={strings.settings.workflows.fieldTrigger} htmlFor="workflow-trigger-type" className="sm:col-span-5">
                <Select
                  id="workflow-trigger-type"
                  name="workflow-trigger-type"
                  value={newTriggerType}
                  onChange={(e) => setNewTriggerType(e.target.value as "webhook" | "schedule" | "agent_event")}
                >
                  <option value="webhook">{strings.settings.workflows.triggerWebhook}</option>
                  <option value="schedule">{strings.settings.workflows.triggerSchedule}</option>
                  <option value="agent_event">{strings.settings.workflows.triggerAgentEvent}</option>
                </Select>
              </Field>
            </div>
            <Field label={strings.settings.workflows.fieldDescription} htmlFor="workflow-description">
              <Input
                id="workflow-description"
                name="workflow-description"
                autoComplete="off"
                placeholder={strings.settings.workflows.placeholderDesc}
                value={newDescription}
                onChange={(e) => setNewDescription(e.target.value)}
              />
            </Field>
            <div className="flex justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={() => setShowCreate(false)}>
                {strings.settings.workflows.cancel}
              </Button>
              <Button
                size="sm"
                variant="primary"
                data-testid="workflow-create-submit"
                onClick={() => void handleCreate()}
              >
                {strings.settings.workflows.create}
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
                  <Badge tone="neutral">{strings.settings.workflows.stepsBadge(wf.steps.length)}</Badge>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => void (wf.enabled ? disable(wf.id) : enable(wf.id))}
                  >
                    <span className={wf.enabled ? "text-status-success" : "text-ink-2"}>
                      {wf.enabled ? strings.settings.workflows.enabled : strings.settings.workflows.disabled}
                    </span>
                  </Button>
                  <IconButton
                    data-testid={`workflow-trigger-${wf.id}`}
                    aria-label={strings.settings.workflows.runAria(wf.name)}
                    title={strings.settings.workflows.runTitle}
                    onClick={() => void trigger(wf.id)}
                  >
                    <Play />
                  </IconButton>
                  <IconButton
                    aria-label={strings.settings.workflows.deleteAria(wf.name)}
                    onClick={async () => {
                      const accepted = await requestConfirmation({
                        title: strings.settings.workflows.deleteTitle(wf.name),
                        description: strings.settings.workflows.deleteDesc,
                        confirmLabel: strings.settings.workflows.deleteLabel,
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
                <span>{strings.settings.workflows.runs(wf.run_count)}</span>
                {wf.last_run_at && <span>{strings.settings.workflows.last(formatDateTime(wf.last_run_at))}</span>}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="text-[11px] text-ink-2">{strings.settings.workflows.footer(total)}</div>
    </section>
  );
}

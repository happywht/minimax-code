/**
 * Scheduled tab — manage cron jobs via `schedule.*` IPC.
 * Includes ScheduledJobRow and TaskStatusDot sub-components.
 */
import { useEffect, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Pencil,
  Play,
  Plus,
  Trash2,
} from "lucide-react";
import { Button, IconButton, Input, Panel } from "../../ui";
import { strings } from "../../ui/strings";
import { useScheduleStore } from "../../stores";
import { toast } from "../layout/ErrorBoundary";
import type { ScheduledJob, ScheduleContext } from "../../types/ipc";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { InlineCode, TabHeader } from "./fields";
import { emptyScheduleContext, validScheduleContext, ScheduleContextFields, ScheduleSnapshot, ScheduleHistory } from "./ScheduleContextFields";
import { ipc } from "../../ipc";
import { StreamEvent } from "../../types/ipc";

export { ScheduledTab };

function ScheduledTab({onOpenSession, active = true}: {onOpenSession?: () => boolean | void; active?: boolean}): JSX.Element {
  const jobs = useScheduleStore((s) => s.jobs);
  const refresh = useScheduleStore((s) => s.refresh);
  const create = useScheduleStore((s) => s.create);
  const remove = useScheduleStore((s) => s.remove);
  const update = useScheduleStore((s) => s.update);
  const setEnabled = useScheduleStore((s) => s.setEnabled);
  const runNow = useScheduleStore((s) => s.runNow);
  const loading = useScheduleStore((s) => s.loading);

  const [draftName, setDraftName] = useState("");
  const [draftCron, setDraftCron] = useState("");
  const [draftPrompt, setDraftPrompt] = useState("");
  const [draftContext, setDraftContext] = useState(emptyScheduleContext);

  useEffect(() => { if (active && jobs.length === 0) void refresh(); }, [active, jobs.length, refresh]);
  useEffect(() => {
    if (!active) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const wake = () => { if (timer) clearTimeout(timer); timer = setTimeout(() => void refresh(), 200); };
    const unsubs = [StreamEvent.RunUpdated, StreamEvent.RunCompleted, StreamEvent.TaskProgress, StreamEvent.ScheduleUpdated].map((event) => ipc.on(event, wake));
    unsubs.push(ipc.onSideCar((event) => { if (event.status === "started") wake(); }));
    return () => { if (timer) clearTimeout(timer); unsubs.forEach((unsub) => unsub()); };
  }, [refresh, active]);

  const handleCreate = async () => {
    const job = await create({ name: draftName.trim(), cron: draftCron.trim(), prompt: draftPrompt.trim(), context: draftContext });
    if (job) {
      toast.success(strings.settings.scheduled.createdToast, job.name);
      setDraftName(""); setDraftCron(""); setDraftPrompt("");
    }
  };

  return (
    <section data-testid="settings-scheduled" className="space-y-4">
      <TabHeader
        title={strings.settings.scheduled.title}
        hint={
          <>
            {strings.settings.scheduled.hintLead}{" "}
            <InlineCode>*/5 * * * *</InlineCode>
            {strings.settings.scheduled.hintTail}
          </>
        }
      />

      <Panel title={strings.settings.scheduled.newTitle}>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-12">
          <label htmlFor="scheduled-job-name" className="sr-only">{strings.settings.scheduled.fieldName}</label>
          <Input
            id="scheduled-job-name"
            name="scheduled-job-name"
            autoComplete="off"
            data-testid="settings-job-name"
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            placeholder={strings.settings.scheduled.placeholderName}
            className="sm:col-span-3"
          />
          <label htmlFor="scheduled-job-cron" className="sr-only">{strings.settings.scheduled.fieldCron}</label>
          <Input
            id="scheduled-job-cron"
            name="scheduled-job-cron"
            autoComplete="off"
            spellCheck={false}
            data-testid="settings-job-cron"
            value={draftCron}
            onChange={(e) => setDraftCron(e.target.value)}
            placeholder={strings.settings.scheduled.placeholderCron}
            className="font-mono sm:col-span-3"
          />
          <label htmlFor="scheduled-job-prompt" className="sr-only">{strings.settings.scheduled.fieldPrompt}</label>
          <Input
            id="scheduled-job-prompt"
            name="scheduled-job-prompt"
            autoComplete="off"
            data-testid="settings-job-prompt"
            value={draftPrompt}
            onChange={(e) => setDraftPrompt(e.target.value)}
            placeholder={strings.settings.scheduled.placeholderPrompt}
            className="sm:col-span-4"
          />
          <Button
            size="sm"
            variant="subtle"
            data-testid="settings-job-add"
            disabled={!draftName.trim() || !draftCron.trim() || !draftPrompt.trim() || !validScheduleContext(draftContext)}
            onClick={() => void handleCreate()}
            icon={<Plus />}
            className="sm:col-span-2"
          >
            {strings.settings.scheduled.create}
          </Button>
        </div>
        <ScheduleContextFields value={draftContext} onChange={setDraftContext} />
      </Panel>

      <ul className="space-y-1.5" data-testid="settings-jobs-list">
        {jobs.length === 0 && !loading && (
          <li className="rounded-lg border border-dashed border-line px-3 py-4 text-center text-xs text-ink-2">
            {strings.settings.scheduled.empty}
          </li>
        )}
        {jobs.map((j) => (
          <ScheduledJobRow
            key={j.id}
            job={j}
            onOpenSession={onOpenSession}
            onToggle={(enabled) => void setEnabled(j.id, enabled)}
            onDelete={async () => {
              const accepted = await requestConfirmation({
                title: strings.settings.scheduled.deleteTitle(j.name),
                description: strings.settings.scheduled.deleteDesc,
                confirmLabel: strings.settings.scheduled.deleteLabel,
              });
              if (accepted) await remove(j.id);
            }}
            onRunNow={() => void runNow(j.id)}
            onUpdate={async (name, cron, prompt, context) => {
              const job = await update(j.id, { name, cron, prompt, context });
              if (job) toast.success(strings.settings.scheduled.updatedToast, job.name);
            }}
          />
        ))}
      </ul>
    </section>
  );
}

function ScheduledJobRow({ job, onToggle, onDelete, onRunNow, onUpdate, onOpenSession }: {
  onOpenSession?: () => boolean | void;
  job: ScheduledJob; onToggle: (enabled: boolean) => void;
  onDelete: () => void; onRunNow: () => void;
  onUpdate: (name: string, cron: string, prompt: string, context: ScheduleContext) => Promise<void>;
}): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState(job.name);
  const [editCron, setEditCron] = useState(job.cron);
  const [editPrompt, setEditPrompt] = useState(job.prompt);
  const [editContext, setEditContext] = useState(job.context ?? emptyScheduleContext());
  const [acknowledged, setAcknowledged] = useState(false);

  const startEditing = () => {
    // Reset the draft to the current definition so reopening after a
    // cancelled edit never shows stale edits.
    setEditName(job.name);
    setEditCron(job.cron);
    setEditPrompt(job.prompt);
    setEditContext(job.context ?? emptyScheduleContext());
    setEditing(true);
  };

  const handleSave = async () => {
    await onUpdate(editName.trim(), editCron.trim(), editPrompt.trim(), editContext);
    setEditing(false);
  };


  return (
    <li
      data-testid={`settings-job-row-${job.id}`}
      className="rounded-lg border border-line bg-surface-2 transition-colors duration-150 hover:border-line-strong"
    >
      <div className="flex items-center gap-2 px-3 py-2 text-sm">
        <IconButton
          data-testid={`settings-job-expand-${job.id}`}
          onClick={() => setExpanded((v) => !v)}
          aria-label={expanded ? strings.settings.scheduled.collapse : strings.settings.scheduled.expand}
          active={expanded}
        >
          {expanded ? <ChevronDown /> : <ChevronRight />}
        </IconButton>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs font-medium text-ink-0">{job.name}</span>
          <span className="block truncate font-mono text-[11px] text-ink-2">
            {job.cron} · {job.prompt || strings.settings.scheduled.noPrompt}
          </span>
        </span>
        <label
          className="inline-flex cursor-pointer items-center gap-1.5 text-[11px] text-ink-2"
          data-testid={`settings-job-toggle-${job.id}`}
        >
          <input
            type="checkbox"
            checked={job.enabled}
            disabled={job.binding_state === "needs_rebind" || (job.binding_state === "requires_review" && !acknowledged)}
            onChange={(e) => { if (e.target.checked && acknowledged) void useScheduleStore.getState().enable(job.id, job.active_occurrence_id ?? undefined); else onToggle(e.target.checked); }}
            className="h-3 w-3 accent-accent"
          />
          {job.enabled ? strings.settings.scheduled.enabled : strings.settings.scheduled.disabled}
        </label>
        <IconButton
          data-testid={`settings-job-edit-${job.id}`}
          onClick={editing ? () => setEditing(false) : startEditing}
          aria-label={strings.settings.scheduled.edit}
        >
          <Pencil />
        </IconButton>
        <IconButton
          data-testid={`settings-job-run-now-${job.id}`}
          onClick={onRunNow}
          disabled={!job.enabled || job.binding_state !== "ready" || !!job.active_occurrence_id}
          aria-label={strings.settings.scheduled.runNowAria}
        >
          <Play />
        </IconButton>
        <IconButton
          data-testid={`settings-job-delete-${job.id}`}
          onClick={onDelete}
          aria-label={strings.settings.scheduled.deleteAria}
        >
          <Trash2 />
        </IconButton>
      </div>
      <ScheduleSnapshot job={job} />
      {job.binding_state === "requires_review" && <label className="px-3 text-xs"><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />已核对 {job.active_occurrence_id} 的副作用，放弃该运行恢复并允许未来任务</label>}
      {editing && (
        <div data-testid={`settings-job-edit-form-${job.id}`} className="border-t border-line px-3 py-2">
          <p className="mb-2 text-[11px] font-medium text-ink-1">
            {strings.settings.scheduled.editTitle}
          </p>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-12">
            <label htmlFor={`scheduled-job-edit-name-${job.id}`} className="sr-only">
              {strings.settings.scheduled.fieldName}
            </label>
            <Input
              id={`scheduled-job-edit-name-${job.id}`}
              autoComplete="off"
              data-testid="settings-job-edit-name"
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              placeholder={strings.settings.scheduled.placeholderName}
              className="sm:col-span-3"
            />
            <label htmlFor={`scheduled-job-edit-cron-${job.id}`} className="sr-only">
              {strings.settings.scheduled.fieldCron}
            </label>
            <Input
              id={`scheduled-job-edit-cron-${job.id}`}
              autoComplete="off"
              spellCheck={false}
              data-testid="settings-job-edit-cron"
              value={editCron}
              onChange={(e) => setEditCron(e.target.value)}
              placeholder={strings.settings.scheduled.placeholderCron}
              className="font-mono sm:col-span-3"
            />
            <label htmlFor={`scheduled-job-edit-prompt-${job.id}`} className="sr-only">
              {strings.settings.scheduled.fieldPrompt}
            </label>
            <Input
              id={`scheduled-job-edit-prompt-${job.id}`}
              autoComplete="off"
              data-testid="settings-job-edit-prompt"
              value={editPrompt}
              onChange={(e) => setEditPrompt(e.target.value)}
              placeholder={strings.settings.scheduled.placeholderPrompt}
              className="sm:col-span-4"
            />
            <div className="flex gap-1.5 sm:col-span-2">
              <Button
                size="sm"
                variant="ghost"
                data-testid="settings-job-edit-cancel"
                onClick={() => setEditing(false)}
              >
                {strings.settings.scheduled.cancel}
              </Button>
              <Button
                size="sm"
                variant="subtle"
                data-testid="settings-job-edit-save"
                disabled={!editName.trim() || !editCron.trim() || !editPrompt.trim() || !validScheduleContext(editContext)}
                onClick={() => void handleSave()}
              >
                {strings.settings.scheduled.save}
              </Button>
            </div>
          </div>
          <ScheduleContextFields value={editContext} onChange={setEditContext} />
        </div>
      )}
      <div data-testid={expanded ? `settings-job-tasks-${job.id}` : undefined} className={expanded ? "border-t border-line py-2" : ""}>
        <ScheduleHistory job={job} expanded={expanded} onOpenSession={onOpenSession} />
      </div>
    </li>
  );
}

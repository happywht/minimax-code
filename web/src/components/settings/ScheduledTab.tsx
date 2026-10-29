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
import { ScheduleCronBuilder } from "./ScheduleCronBuilder";
import { ipc } from "../../ipc";
import { StreamEvent } from "../../types/ipc";

export { ScheduledTab };

// Deliberately process-local: drafts never leave this browser page or survive reload.
type CreateDraft = { name: string; cron: string; prompt: string; context: ScheduleContext };
type EditDraft = CreateDraft & { revision: number | null; service: string | null; initial: string };
const createDrafts = new Map<string, CreateDraft>();
const editDrafts = new Map<string, EditDraft>();

function ScheduledTab({onOpenSession, active = true, serviceId, draftScope, target}: {target?: { jobId: string; occurrenceId?: string } | null; onOpenSession?: () => boolean | void; active?: boolean; serviceId?: string; draftScope?: string}): JSX.Element {
  const jobs = useScheduleStore((s) => s.jobs);
  const refresh = useScheduleStore((s) => s.refresh);
  const create = useScheduleStore((s) => s.create);
  const remove = useScheduleStore((s) => s.remove);
  const update = useScheduleStore((s) => s.update);
  const setEnabled = useScheduleStore((s) => s.setEnabled);
  const runNow = useScheduleStore((s) => s.runNow);
  const loading = useScheduleStore((s) => s.loading);

  const [, setDraftListVersion] = useState(0);
  const [draftName, setDraftName] = useState(() => draftScope ? createDrafts.get(draftScope)?.name ?? "" : "");
  const [draftCron, setDraftCron] = useState(() => draftScope ? createDrafts.get(draftScope)?.cron ?? "" : "");
  const [draftPrompt, setDraftPrompt] = useState(() => draftScope ? createDrafts.get(draftScope)?.prompt ?? "" : "");
  const [draftContext, setDraftContext] = useState(() => (draftScope && createDrafts.get(draftScope)?.context) || emptyScheduleContext());

  useEffect(() => {
    if (draftScope) createDrafts.set(draftScope, { name: draftName, cron: draftCron, prompt: draftPrompt, context: draftContext });
  }, [draftScope, draftName, draftCron, draftPrompt, draftContext]);

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
      setDraftName(""); setDraftCron(""); setDraftPrompt(""); setDraftContext(emptyScheduleContext());
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
        <ScheduleCronBuilder onApply={setDraftCron} timezone={draftContext.timezone} />
        <ScheduleContextFields value={draftContext} onChange={setDraftContext} />
      </Panel>

      {draftScope && !loading && [...editDrafts.entries()].filter(([key]) => {
        const [scope, id] = JSON.parse(key) as [string, string];
        return scope === draftScope && !jobs.some((job) => job.id === id);
      }).map(([key, draft]) => <div key={key} className="space-y-2 rounded-lg border border-status-warning p-3 text-xs" role="status">
        <p>原计划当前不在列表中，可能已删除或尚未载入。草稿保留在此供复制，不能直接保存。</p>
        <pre className="whitespace-pre-wrap break-all">{JSON.stringify(draft, null, 2)}</pre>
        <Button type="button" size="sm" variant="secondary" onClick={async () => {
          if (await requestConfirmation({ title: "放弃此草稿？", description: "草稿内容将丢失。", confirmLabel: "放弃草稿" })) {
            editDrafts.delete(key); setDraftListVersion((version) => version + 1);
          }
        }}>放弃草稿</Button>
      </div>)}
      {target && !loading && !jobs.some((job) => job.id === target.jobId) && <p role="status">通知关联的计划当前不可用，可能已删除。</p>}
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
            targetOccurrenceId={target?.jobId === j.id ? target.occurrenceId : undefined}
            targetJob={target?.jobId === j.id}
            serviceId={serviceId}
            draftKey={draftScope ? JSON.stringify([draftScope, j.id]) : undefined}
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
            onUpdate={async (revision, name, cron, prompt, context, expectedService) => {
              const result = await update(j.id, revision, { name, cron, prompt, context }, expectedService);
              if (result.job) toast.success(strings.settings.scheduled.updatedToast, result.job.name);
              return result;
            }}
          />
        ))}
      </ul>
    </section>
  );
}

function ScheduledJobRow({ job, onToggle, onDelete, onRunNow, onUpdate, onOpenSession, serviceId, draftKey, targetJob, targetOccurrenceId }: {
  targetJob?: boolean; targetOccurrenceId?: string;
  onOpenSession?: () => boolean | void;
  job: ScheduledJob; serviceId?: string; draftKey?: string; onToggle: (enabled: boolean) => void;
  onDelete: () => void; onRunNow: () => void;
  onUpdate: (revision: number, name: string, cron: string, prompt: string, context: ScheduleContext, expectedService: string) => Promise<{ job: ScheduledJob | null; error?: string }>;
}): JSX.Element {
  const [restored] = useState(() => draftKey ? editDrafts.get(draftKey) : undefined);
  const [expanded, setExpanded] = useState(!!targetJob);
  useEffect(() => { if (targetJob) setExpanded(true); }, [targetJob, targetOccurrenceId]);
  const [editing, setEditing] = useState(!!restored);
  const [editName, setEditName] = useState(restored?.name ?? job.name);
  const [editCron, setEditCron] = useState(restored?.cron ?? job.cron);
  const [editPrompt, setEditPrompt] = useState(restored?.prompt ?? job.prompt);
  const [editContext, setEditContext] = useState(restored?.context ?? job.context ?? emptyScheduleContext());
  const [editRevision, setEditRevision] = useState<number | null>(restored?.revision ?? null);
  const [editService, setEditService] = useState<string | null>(restored?.service ?? null);
  const [initialDraft, setInitialDraft] = useState(restored?.initial ?? "");
  const [editError, setEditError] = useState("");
  const [saving, setSaving] = useState(false);
  const draftSignature = JSON.stringify([editName, editCron, editPrompt, editContext]);
  const changed = editing && draftSignature !== initialDraft;
  const changedOnServer = editRevision !== null && job.revision !== editRevision;

  useEffect(() => {
    if (!draftKey) return;
    if (editing && changed) editDrafts.set(draftKey, { name: editName, cron: editCron, prompt: editPrompt, context: editContext, revision: editRevision, service: editService, initial: initialDraft });
    else editDrafts.delete(draftKey);
  }, [draftKey, editing, changed, editName, editCron, editPrompt, editContext, editRevision, editService, initialDraft]);

  const startEditing = () => {
    // Reset the draft to the current definition so reopening after a
    // cancelled edit never shows stale edits.
    setEditName(job.name);
    setEditCron(job.cron);
    setEditPrompt(job.prompt);
    const context = job.context ?? emptyScheduleContext();
    setEditContext(context);
    setEditRevision(job.revision ?? null);
    setEditService(serviceId ?? null);
    setInitialDraft(JSON.stringify([job.name, job.cron, job.prompt, context]));
    setEditError("");
    setEditing(true);
  };

  const cancelEdit = async () => {
    if (saving) return;
    if (changed && !await requestConfirmation({ title: "放弃定时计划草稿？", description: "未保存的编辑将丢失。", confirmLabel: "放弃草稿" })) return;
    if (draftKey) editDrafts.delete(draftKey);
    setEditing(false); setEditError("");
  };
  const reloadEdit = async () => {
    if (saving) return;
    if (changed && !await requestConfirmation({ title: "载入最新计划？", description: "当前编辑草稿将被最新版本替换。请先复制需要保留的内容。", confirmLabel: "载入最新版本" })) return;
    startEditing();
  };
  const handleSave = async () => {
    if (saving || editRevision === null || !editService || editService !== serviceId) return;
    setSaving(true); setEditError("");
    try {
      const result = await onUpdate(editRevision, editName.trim(), editCron.trim(), editPrompt.trim(), editContext, editService);
      if (result.job) { if (draftKey) editDrafts.delete(draftKey); setEditing(false); }
      else setEditError(result.error || "保存未确认；请刷新计划并核对后再操作。");
    } catch (error) { setEditError(error instanceof Error ? error.message : String(error)); }
    finally { setSaving(false); }
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
            disabled={job.binding_state === "needs_rebind" || job.binding_state === "requires_review"}
            onChange={(e) => onToggle(e.target.checked)}
            className="h-3 w-3 accent-accent"
          />
          {job.enabled ? strings.settings.scheduled.enabled : strings.settings.scheduled.disabled}
        </label>
        <IconButton
          data-testid={`settings-job-edit-${job.id}`}
          onClick={editing ? () => void cancelEdit() : startEditing}
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

      {editing && (
        <div data-testid={`settings-job-edit-form-${job.id}`} className="border-t border-line px-3 py-2">
          <fieldset disabled={saving}>
          <p className="mb-2 text-[11px] font-medium text-ink-1">
            {strings.settings.scheduled.editTitle}
          </p>
          <p className="mb-2 text-xs text-ink-2">编辑基于版本 {editRevision ?? "未知"}。已接单运行保留原快照；未接单的旧发生会被替代。保存会重新捕获当前权限与项目配置，不会自动启用计划。</p>
          {!editService && <p role="alert" className="text-status-warning">服务身份未确认，无法保存；请重连后重新编辑。</p>}
          {editRevision === null && <p role="alert" className="text-status-warning">版本未知，无法安全保存；请刷新后重新编辑。</p>}
          {changedOnServer && <div role="status" className="space-y-1 text-status-warning"><p>计划已更新到版本 {job.revision ?? "未知"}。当前草稿仍保留，不能直接覆盖新版本。</p><Button type="button" size="sm" variant="secondary" disabled={saving} onClick={() => void reloadEdit()}>载入最新版本</Button></div>}
          {editError && <p role="alert" className="break-words text-status-error">保存失败或结果未确认：{editError}。草稿仍保留；请刷新并核对版本后处理。</p>}
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
                disabled={saving}
                onClick={() => void cancelEdit()}
              >
                {strings.settings.scheduled.cancel}
              </Button>
              <Button
                size="sm"
                variant="subtle"
                data-testid="settings-job-edit-save"
                disabled={saving || changedOnServer || editRevision === null || !editService || editService !== serviceId || !editName.trim() || !editCron.trim() || !editPrompt.trim() || !validScheduleContext(editContext)}
                loading={saving}
                onClick={() => void handleSave()}
              >
                {strings.settings.scheduled.save}
              </Button>
            </div>
          </div>
          <ScheduleCronBuilder onApply={setEditCron} timezone={editContext.timezone} />
          <ScheduleContextFields value={editContext} onChange={setEditContext} />
          </fieldset>
        </div>
      )}
      <div data-testid={expanded ? `settings-job-tasks-${job.id}` : undefined} className={expanded ? "border-t border-line py-2" : ""}>
        <ScheduleHistory job={job} expanded={expanded} targetOccurrenceId={targetOccurrenceId} serviceId={serviceId} onOpenSession={onOpenSession} />
      </div>
    </li>
  );
}

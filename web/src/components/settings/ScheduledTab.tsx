/**
 * Scheduled tab — manage cron jobs via `schedule.*` IPC.
 * Includes ScheduledJobRow and TaskStatusDot sub-components.
 */
import { useEffect, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Copy,
  Pencil,
  Play,
  Plus,
  Trash2,
} from "lucide-react";
import { Button, IconButton, Input, Panel } from "../../ui";
import { strings } from "../../ui/strings";
import { useScheduleStore } from "../../stores";
import { useSessionStore } from "../../stores/sessionStore";
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
type CreateDraft = { name: string; cron: string; prompt: string; context: ScheduleContext; enabled?: boolean };
type EditDraft = CreateDraft & { revision: number | null; service: string | null; initial: string };
const createDrafts = new Map<string, CreateDraft>();
const editDrafts = new Map<string, EditDraft>();
// A lost create reply may have created a job. Retain the fence across remounts
// for this service scope until the user has checked the list.
const uncertainCreates = new Set<string>();

function ScheduledTab({onOpenSession, onDraftEdit, active = true, serviceId, draftScope, target}: {target?: { jobId: string; occurrenceId?: string } | null; onOpenSession?: () => boolean | void; onDraftEdit?: () => void; active?: boolean; serviceId?: string; draftScope?: string}): JSX.Element {
  const jobs = useScheduleStore((s) => s.jobs);
  const refresh = useScheduleStore((s) => s.refresh);
  const create = useScheduleStore((s) => s.create);
  const remove = useScheduleStore((s) => s.remove);
  const update = useScheduleStore((s) => s.update);
  const setEnabled = useScheduleStore((s) => s.setEnabled);
  const runNow = useScheduleStore((s) => s.runNow);
  const loading = useScheduleStore((s) => s.loading);
  const projects = useSessionStore((s) => s.projects);
  const currentProject = useSessionStore((s) => s.currentProjectId);
  const [query, setQuery] = useState("");
  const [projectFilter, setProjectFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const projectIds = [...new Set(jobs.flatMap((job) => job.context?.project_id ? [job.context.project_id] : []))];
  const matchingJobs = jobs.filter((job) => {
    const text = query.trim().toLocaleLowerCase();
    const matchesText = !text || [job.name, job.prompt, job.cron].some((value) => value.toLocaleLowerCase().includes(text));
    const matchesProject = projectFilter === "all" || (projectFilter === "unbound" ? !job.context?.project_id : `project:${job.context?.project_id}` === projectFilter);
    const matchesStatus = statusFilter === "all" || (statusFilter === "attention"
      ? job.binding_state !== "ready"
      : statusFilter === "enabled" ? job.enabled : !job.enabled);
    return matchesText && matchesProject && matchesStatus;
  });
  // Notification links must remain reachable even when the current filters exclude the job.
  const visibleIds = new Set(matchingJobs.map((job) => job.id));
  const targetOutsideFilter = !!target && jobs.some((job) => job.id === target.jobId) && !visibleIds.has(target.jobId);
  if (targetOutsideFilter && target) visibleIds.add(target.jobId);

  const [, setDraftListVersion] = useState(0);
  const [draftName, setDraftName] = useState(() => draftScope ? createDrafts.get(draftScope)?.name ?? "" : "");
  const [draftCron, setDraftCron] = useState(() => draftScope ? createDrafts.get(draftScope)?.cron ?? "" : "");
  const [draftPrompt, setDraftPrompt] = useState(() => draftScope ? createDrafts.get(draftScope)?.prompt ?? "" : "");
  const [draftContext, setDraftContext] = useState(() => (draftScope && createDrafts.get(draftScope)?.context) || emptyScheduleContext());
  const [draftEnabled, setDraftEnabled] = useState(() => !!(draftScope && createDrafts.get(draftScope)?.enabled));
  const createScope = JSON.stringify([ipc.agentBaseUrl, serviceId]);
  const [creating, setCreating] = useState(false), [createUncertain, setCreateUncertain] = useState(() => uncertainCreates.has(createScope));
  const createInFlight = useRef(false), copyEpoch = useRef(0);
  const draftSignature = JSON.stringify([draftName, draftCron, draftPrompt, draftContext, draftEnabled]);
  const draftSignatureRef = useRef(draftSignature); draftSignatureRef.current = draftSignature;
  useEffect(() => () => { copyEpoch.current++; }, []);
  useEffect(() => { setCreateUncertain(uncertainCreates.has(createScope)); }, [createScope]);

  useEffect(() => {
    if (draftScope) createDrafts.set(draftScope, { name: draftName, cron: draftCron, prompt: draftPrompt, context: draftContext, enabled: draftEnabled });
  }, [draftScope, draftName, draftCron, draftPrompt, draftContext, draftEnabled]);

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
    if (createInFlight.current || createUncertain || uncertainCreates.has(createScope) || !serviceId || !active) return;
    createInFlight.current = true; uncertainCreates.add(createScope); setCreating(true);
    try {
      const job = await create({ name: draftName.trim(), cron: draftCron.trim(), prompt: draftPrompt.trim(), context: draftContext, enabled: draftEnabled, expected_service_id: serviceId });
      if (job) {
        uncertainCreates.delete(createScope);
        toast.success(strings.settings.scheduled.createdToast, job.name);
        setDraftName(""); setDraftCron(""); setDraftPrompt(""); setDraftContext(emptyScheduleContext()); setDraftEnabled(false);
      } else setCreateUncertain(true);
    } finally { createInFlight.current = false; setCreating(false); }
  };
  const copyToDraft = async (job: ScheduledJob) => {
    if (creating || createUncertain) return;
    const ticket = ++copyEpoch.current, host = ipc.agentBaseUrl, signature = draftSignature;
    if ((draftName || draftCron || draftPrompt || validScheduleContext(draftContext)) && !await requestConfirmation({ title: "替换新建草稿？", description: "当前新建草稿将被所选计划替换。", confirmLabel: "替换草稿" })) return;
    if (ticket !== copyEpoch.current || ipc.agentBaseUrl !== host || !active || createInFlight.current || uncertainCreates.has(createScope)) return;
    if (signature !== draftSignatureRef.current) { toast.error("草稿已变化", "请重新选择复制，避免覆盖新的编辑。"); return; }
    const source = job.context;
    setDraftName(`${job.name}（副本）`); setDraftCron(job.cron); setDraftPrompt(job.prompt);
    setDraftContext(source ? { project_id: source.project_id, provider_id: source.provider_id, model: source.model,
      reasoning_effort: source.reasoning_effort, mode: source.mode, budget_tokens: source.budget_tokens,
      timezone: source.timezone, restart_missed_policy: source.restart_missed_policy } : emptyScheduleContext());
    setDraftEnabled(false); onDraftEdit?.();
    document.getElementById("scheduled-job-name")?.focus();
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
        <fieldset disabled={creating}>
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
            disabled={!active || !serviceId || creating || createUncertain || !draftName.trim() || !draftCron.trim() || !draftPrompt.trim() || !validScheduleContext(draftContext)}
            loading={creating}
            onClick={() => void handleCreate()}
            icon={<Plus />}
            className="sm:col-span-2"
          >
            {strings.settings.scheduled.create}
          </Button>
        </div>
        <ScheduleCronBuilder onApply={setDraftCron} timezone={draftContext.timezone} />
        <ScheduleContextFields value={draftContext} onChange={setDraftContext} />
        <label className="mt-2 flex items-center gap-2 text-xs"><input type="checkbox" checked={draftEnabled} onChange={(event) => setDraftEnabled(event.target.checked)} />创建后启用计划</label>
        <p className="text-xs text-ink-2">默认先创建为停用，核对后可启用；启用的计划会在到期时执行。</p>
        {createUncertain && <div className="mt-2 text-xs text-status-warning" role="alert"><p>创建结果未确认。请刷新计划列表，核对是否已创建，避免重复计划。</p><Button type="button" size="sm" variant="secondary" onClick={async () => { await refresh(); }}>刷新计划列表</Button> <Button type="button" size="sm" variant="secondary" onClick={async () => { if (await requestConfirmation({title: "已核对创建结果？", description: "只有确认不会造成重复计划时，才可再次提交此草稿。", confirmLabel: "已核对，允许再次提交"})) { uncertainCreates.delete(createScope); setCreateUncertain(false); } }}>已核对，允许再次提交</Button></div>}
        </fieldset>
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
      <div className="flex flex-wrap items-end gap-2 text-xs" aria-label="筛选定时计划" data-schedule-filter>
        <label>搜索计划<Input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="名称、提示词或 Cron" /></label>
        <label>项目<select className="block rounded border border-line bg-surface-2 p-1" value={projectFilter} onChange={(event) => setProjectFilter(event.target.value)}>
          <option value="all">全部项目</option>
          {projectIds.map((id) => <option key={id} value={`project:${id}`}>{projects.find((project) => project.id === id)?.name ?? id}{id === currentProject ? "（当前项目）" : ""}</option>)}
          {jobs.some((job) => !job.context?.project_id) && <option value="unbound">未绑定项目</option>}
        </select></label>
        <label>状态<select className="block rounded border border-line bg-surface-2 p-1" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
          <option value="all">全部状态</option><option value="enabled">已启用</option><option value="disabled">已停用</option><option value="attention">需要处理或绑定</option>
        </select></label>
        <Button type="button" size="sm" variant="ghost" onClick={() => { setQuery(""); setProjectFilter("all"); setStatusFilter("all"); }}>清除筛选</Button>
        <Button type="button" size="sm" variant="secondary" disabled={!active || loading} loading={loading} onClick={() => void refresh()}>刷新计划状态</Button>
        <span role="status">匹配 {matchingJobs.length} / {jobs.length} 个计划{targetOutsideFilter ? "；另显示通知关联计划" : ""}</span>
      </div>
      <ul className="space-y-1.5" data-testid="settings-jobs-list">
        {jobs.length === 0 && !loading && (
          <li className="rounded-lg border border-dashed border-line px-3 py-4 text-center text-xs text-ink-2">
            {strings.settings.scheduled.empty}
          </li>
        )}
        {jobs.length > 0 && matchingJobs.length === 0 && !targetOutsideFilter && <li className="rounded-lg border border-dashed border-line px-3 py-4 text-center text-xs text-ink-2">没有匹配的计划，请调整筛选条件。</li>}
        {jobs.map((j) => (
          <ScheduledJobRow
            key={j.id}
            job={j}
            hidden={!visibleIds.has(j.id)}
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
            onCopy={() => void copyToDraft(j)}
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

function ScheduledJobRow({ job, onToggle, onDelete, onRunNow, onCopy, onUpdate, onOpenSession, serviceId, draftKey, targetJob, targetOccurrenceId, hidden }: {
  hidden?: boolean;
  targetJob?: boolean; targetOccurrenceId?: string;
  onOpenSession?: () => boolean | void;
  job: ScheduledJob; serviceId?: string; draftKey?: string; onToggle: (enabled: boolean) => void;
  onDelete: () => void; onRunNow: () => void;
  onCopy: () => void;
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
      hidden={hidden}
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
          onClick={onCopy}
          aria-label="复制计划到新建草稿"
        ><Copy /></IconButton>
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

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
  Plus,
  Trash2,
} from "lucide-react";
import { Button, IconButton, Input, Panel } from "../../ui";
import { strings } from "../../ui/strings";
import { useScheduleStore } from "../../stores";
import { useSessionStore } from "../../stores/sessionStore";
import { toast } from "../layout/ErrorBoundary";
import type { ScheduledJob, ScheduleContext, ScheduleDefinition, ScheduleOccurrence } from "../../types/ipc";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { InlineCode, TabHeader } from "./fields";
import { emptyScheduleContext, validScheduleContext, ScheduleContextFields, ScheduleSnapshot, ScheduleHistory } from "./ScheduleContextFields";
import { ScheduleCronBuilder } from "./ScheduleCronBuilder";
import { ScheduleTimePreview } from "./ScheduleTimePreview";
import { ScheduleManualTrigger } from "./ScheduleManualTrigger";
import { ipc } from "../../ipc";
import { StreamEvent } from "../../types/ipc";

export { ScheduledTab };

// Deliberately process-local: drafts never leave this browser page or survive reload.
type CreateDraft = { name: string; cron: string; prompt: string; context: ScheduleContext; enabled?: boolean };
type EditDraft = CreateDraft & { revision: number | null; service: string | null; initial: string };
const createDrafts = new Map<string, CreateDraft>();
const editDrafts = new Map<string, EditDraft>();
type CreateIntent = ScheduleDefinition & { enabled: boolean; expected_service_id: string; idempotency_key: string };
const createStorageKey = (scope: string) => `minimax-code:schedule-create:${scope}`;
function readPendingCreate(scope: string): CreateIntent | null | "unavailable" {
  try {
    const raw = sessionStorage.getItem(createStorageKey(scope));
    if (!raw) return null;
    const value = JSON.parse(raw) as CreateIntent;
    if (!value || typeof value.idempotency_key !== "string" || !value.idempotency_key ||
        typeof value.expected_service_id !== "string" || !value.context || typeof value.name !== "string" ||
        typeof value.cron !== "string" || typeof value.prompt !== "string" || typeof value.enabled !== "boolean") return "unavailable";
    return value;
  } catch { return "unavailable"; }
}

function ScheduledTab({onOpenSession, onDraftEdit, active = true, serviceId, draftScope, target}: {target?: { jobId: string; occurrenceId?: string } | null; onOpenSession?: () => boolean | void; onDraftEdit?: () => void; active?: boolean; serviceId?: string; draftScope?: string}): JSX.Element {
  const jobs = useScheduleStore((s) => s.jobs);
  const refresh = useScheduleStore((s) => s.refresh);
  const create = useScheduleStore((s) => s.create);
  const remove = useScheduleStore((s) => s.remove);
  const update = useScheduleStore((s) => s.update);
  const setEnabled = useScheduleStore((s) => s.setEnabled);
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
  const [creating, setCreating] = useState(false), [pendingCreate, setPendingCreate] = useState<CreateIntent | null | "unavailable">(() => readPendingCreate(createScope));
  const createInFlight = useRef(false), copyEpoch = useRef(0);
  const draftSignature = JSON.stringify([draftName, draftCron, draftPrompt, draftContext, draftEnabled]);
  const draftSignatureRef = useRef(draftSignature); draftSignatureRef.current = draftSignature;
  const scopeSignature = JSON.stringify([ipc.agentBaseUrl, serviceId, active]);
  const scopeSignatureRef = useRef(scopeSignature); scopeSignatureRef.current = scopeSignature;
  useEffect(() => () => { copyEpoch.current++; }, []);
  useEffect(() => { copyEpoch.current++; }, [scopeSignature]);
  useEffect(() => ipc.onSideCar(() => { copyEpoch.current++; }), []);
  useEffect(() => { setPendingCreate(readPendingCreate(createScope)); }, [createScope]);

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

  const submitCreate = async (intent: CreateIntent) => {
    if (createInFlight.current || !active || !serviceId || intent.expected_service_id !== serviceId) return;
    createInFlight.current = true; setCreating(true);
    try {
      const job = await create(intent);
      if (job) {
        try { sessionStorage.removeItem(createStorageKey(createScope)); setPendingCreate(null); }
        catch { setPendingCreate(intent); toast.error("无法清除创建回执", "请保留原请求键并重新读取结果。"); return; }
        toast.success(job.deleted_at ? "原计划已删除" : strings.settings.scheduled.createdToast, `${job.name} · ${job.id}`);
        setDraftName(""); setDraftCron(""); setDraftPrompt(""); setDraftContext(emptyScheduleContext()); setDraftEnabled(false);
      } else setPendingCreate(intent);
    } finally { createInFlight.current = false; setCreating(false); }
  };
  const handleCreate = async () => {
    if (createInFlight.current || pendingCreate || !serviceId || !active) return;
    const saved = readPendingCreate(createScope);
    if (saved) { setPendingCreate(saved); return; }
    const context: ScheduleContext = { project_id: draftContext.project_id, provider_id: draftContext.provider_id,
      model: draftContext.model, reasoning_effort: draftContext.reasoning_effort, mode: draftContext.mode,
      budget_tokens: draftContext.budget_tokens, timezone: draftContext.timezone,
      restart_missed_policy: draftContext.restart_missed_policy };
    const intent: CreateIntent = { name: draftName.trim(), cron: draftCron.trim(), prompt: draftPrompt.trim(), context,
      enabled: draftEnabled, expected_service_id: serviceId, idempotency_key: crypto.randomUUID() };
    try { sessionStorage.setItem(createStorageKey(createScope), JSON.stringify(intent)); }
    catch { setPendingCreate("unavailable"); toast.error("无法保存创建请求", "浏览器存储不可用，未提交计划。"); return; }
    setPendingCreate(intent);
    await submitCreate(intent);
  };
  const copyToDraft = async (job: ScheduledJob) => {
    if (creating || pendingCreate) return;
    const ticket = ++copyEpoch.current, host = ipc.agentBaseUrl, signature = draftSignature;
    if ((draftName || draftCron || draftPrompt || validScheduleContext(draftContext)) && !await requestConfirmation({ title: "替换新建草稿？", description: "当前新建草稿将被所选计划替换。", confirmLabel: "替换草稿" })) return;
    if (ticket !== copyEpoch.current || ipc.agentBaseUrl !== host || !active || createInFlight.current || readPendingCreate(createScope)) return;
    if (signature !== draftSignatureRef.current) { toast.error("草稿已变化", "请重新选择复制，避免覆盖新的编辑。"); return; }
    const source = job.context;
    setDraftName(`${job.name}（副本）`); setDraftCron(job.cron); setDraftPrompt(job.prompt);
    setDraftContext(source ? { project_id: source.project_id, provider_id: source.provider_id, model: source.model,
      reasoning_effort: source.reasoning_effort, mode: source.mode, budget_tokens: source.budget_tokens,
      timezone: source.timezone, restart_missed_policy: source.restart_missed_policy } : emptyScheduleContext());
    setDraftEnabled(false); onDraftEdit?.();
    document.getElementById("scheduled-job-name")?.focus();
  };

  const followupToDraft = async (job: ScheduledJob, occurrence: ScheduleOccurrence, originalText: string, status: string) => {
    const source = occurrence.context, runId = occurrence.run_id;
    if (!serviceId || !active || creating || pendingCreate || !job.revision || !runId || !source?.project_id || !source.workspace || source.host !== "local" || source.engine !== "native" || !["completed", "failed", "cancelled", "interrupted", "budget_paused"].includes(status)) return;
    const ticket = ++copyEpoch.current, host = ipc.agentBaseUrl, signature = draftSignature, scope = scopeSignature;
    const current = () => ticket === copyEpoch.current && scopeSignatureRef.current === scope && ipc.agentBaseUrl === host && !createInFlight.current;
    const excerpt = Array.from(originalText.slice(0, 4000)).slice(0, 2000).join("");
    const accepted = await requestConfirmation({ title: "用本次结果准备后续计划草稿？", description: `将运行 ${runId} 的结果前 2000 字符放入新建计划草稿。内容可能含敏感信息；${draftName || draftCron || draftPrompt || validScheduleContext(draftContext) ? "现有新建草稿会被替换。" : ""}计划默认停用，不会自动创建或运行；请编辑并核对配置。`, confirmLabel: "准备草稿" });
    if (!accepted || !current()) return;
    try {
      const [identity, snapshot, receipt, session, projectList, jobList] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getRunSteps(runId), typedIPC.getRun(runId), typedIPC.getSession(occurrence.session_id), typedIPC.listProjects(), typedIPC.listJobs()]);
      const after = await typedIPC.getServiceIdentity();
      if (!current()) return;
      const sameRun = (run: typeof snapshot.run) => {
        const schedule = run.metadata?.schedule as { job_id?: string; occurrence_id?: string; revision?: number } | undefined;
        return run.id === runId && run.session_id === occurrence.session_id && run.metadata?.project_id === source.project_id &&
          run.metadata?.workspace_snapshot === source.workspace && run.metadata?.model_snapshot?.engine === "native" &&
          schedule?.job_id === job.id && schedule.occurrence_id === occurrence.id && schedule.revision === occurrence.revision && run.status === status;
      };
      const result = receipt.result;
      const latest = jobList.jobs.find((item) => item.id === job.id);
      if (identity.service_id !== serviceId || after.service_id !== serviceId || !sameRun(snapshot.run) || !sameRun(receipt.run) ||
          snapshot.steps.some((step) => step.run_id !== runId || step.session_id !== occurrence.session_id) ||
          session.session.id !== occurrence.session_id || session.session.project_id !== source.project_id ||
          !projectList.projects.some((project) => project.id === source.project_id && !project.archived && project.root_path === source.workspace) ||
          !result || !("text" in result) || result.run_id !== runId || result.session_id !== occurrence.session_id || result.text !== originalText ||
          !latest || latest.revision !== job.revision || latest.cron !== job.cron || latest.name !== job.name ||
          signature !== draftSignatureRef.current || readPendingCreate(createScope)) throw new Error("来源、服务或草稿已变化，请重新读取并核对。");
      setDraftName(`${job.name}（后续）`); setDraftCron(job.cron);
      setDraftPrompt(`请先核对下列历史运行结果和可能的副作用，再根据需要执行后续工作。\n来源：计划 ${job.id}，发生 ${occurrence.id}，运行 ${runId}，状态 ${status}。\n结果摘录${excerpt.length < originalText.length ? "（仅前 2000 字符）" : ""}：\n${excerpt}`);
      setDraftContext({ project_id: source.project_id, provider_id: source.provider_id, model: source.model,
        reasoning_effort: source.reasoning_effort, mode: source.mode, budget_tokens: source.budget_tokens,
        timezone: source.timezone, restart_missed_policy: source.restart_missed_policy });
      setDraftEnabled(false); onDraftEdit?.(); document.getElementById("scheduled-job-name")?.focus();
      toast.success("已准备后续计划草稿", "请编辑并核对项目、配置、预算和权限；尚未创建或运行计划。");
    } catch (failure) { if (current()) toast.error("无法准备后续草稿", failure instanceof Error ? failure.message : String(failure)); }
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
        <fieldset disabled={creating || !!pendingCreate}>
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
            disabled={!active || !serviceId || creating || !!pendingCreate || !draftName.trim() || !draftCron.trim() || !draftPrompt.trim() || !validScheduleContext(draftContext)}
            loading={creating}
            onClick={() => void handleCreate()}
            icon={<Plus />}
            className="sm:col-span-2"
          >
            {strings.settings.scheduled.create}
          </Button>
        </div>
        <ScheduleCronBuilder onApply={setDraftCron} timezone={draftContext.timezone} />
        <ScheduleTimePreview cron={draftCron} timezone={draftContext.timezone} serviceId={serviceId} />
        <ScheduleContextFields value={draftContext} onChange={setDraftContext} />
        <label className="mt-2 flex items-center gap-2 text-xs"><input type="checkbox" checked={draftEnabled} onChange={(event) => setDraftEnabled(event.target.checked)} />创建后启用计划</label>
        <p className="text-xs text-ink-2">默认先创建为停用，核对后可启用；启用的计划会在到期时执行。</p>
        </fieldset>
        {pendingCreate && <div className="mt-2 text-xs text-status-warning" role="alert">
          {pendingCreate === "unavailable" ? <><p>无法读取浏览器中的创建请求；请检查浏览器存储并核对计划列表，避免重复创建。</p><Button type="button" size="sm" onClick={() => setPendingCreate(readPendingCreate(createScope))}>重新读取浏览器存储</Button></> : <><p>创建结果待确认：{pendingCreate.name} · 请求 {pendingCreate.idempotency_key}。重试会使用保存的原始请求；如果此前未创建，重试将创建计划。</p><Button type="button" size="sm" variant="secondary" disabled={!active || creating || pendingCreate.expected_service_id !== serviceId} loading={creating} onClick={() => void submitCreate(pendingCreate)}>核对或安全重试原请求</Button> <Button type="button" size="sm" variant="ghost" disabled={creating} onClick={async () => {
            const intent = pendingCreate;
            if (!await requestConfirmation({title:"放弃原创建请求？", description:"原计划可能已创建或启用。请先核对列表；放弃后使用新请求可能创建另一个计划。", confirmLabel:"已核对，放弃原请求"})) return;
            try { sessionStorage.removeItem(createStorageKey(createScope)); }
            catch { toast.error("无法清除请求", "请检查浏览器存储。"); return; }
            setDraftName(intent.name); setDraftCron(intent.cron); setDraftPrompt(intent.prompt); setDraftContext(intent.context); setDraftEnabled(intent.enabled); setPendingCreate(null); onDraftEdit?.();
          }}>放弃请求并编辑草稿</Button></>}
        </div>}
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
            onCopy={() => void copyToDraft(j)}
            onFollowup={(occurrence, text, status) => void followupToDraft(j, occurrence, text, status)}
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

function ScheduledJobRow({ job, onToggle, onDelete, onCopy, onFollowup, onUpdate, onOpenSession, serviceId, draftKey, targetJob, targetOccurrenceId, hidden }: {
  hidden?: boolean;
  targetJob?: boolean; targetOccurrenceId?: string;
  onOpenSession?: () => boolean | void;
  job: ScheduledJob; serviceId?: string; draftKey?: string; onToggle: (enabled: boolean) => void;
  onDelete: () => void;
  onCopy: () => void;
  onFollowup: (occurrence: ScheduleOccurrence, text: string, status: string) => void;
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
          data-testid={`settings-job-delete-${job.id}`}
          onClick={onDelete}
          aria-label={strings.settings.scheduled.deleteAria}
        >
          <Trash2 />
        </IconButton>
      </div>
      <ScheduleSnapshot job={job} />
      <ScheduleManualTrigger job={job} serviceId={serviceId} />

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
          <ScheduleTimePreview cron={editCron} timezone={editContext.timezone} serviceId={serviceId} />
          <ScheduleContextFields value={editContext} onChange={setEditContext} />
          </fieldset>
        </div>
      )}
      <div data-testid={expanded ? `settings-job-tasks-${job.id}` : undefined} className={expanded ? "border-t border-line py-2" : ""}>
        <ScheduleHistory job={job} expanded={expanded} targetOccurrenceId={targetOccurrenceId} serviceId={serviceId} onOpenSession={onOpenSession} onFollowup={onFollowup} />
      </div>
    </li>
  );
}

/**
 * Scheduled tab — manage cron jobs via `schedule.*` IPC.
 * Includes ScheduledJobRow and TaskStatusDot sub-components.
 */
import { useEffect, useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Play,
  Save,
  Trash2,
} from "lucide-react";
import { useScheduleStore, useTaskStore } from "../../stores";
import { toast } from "../ErrorBoundary";
import type { ScheduledJob } from "../../types/ipc";

export { ScheduledTab };

function ScheduledTab(): JSX.Element {
  const jobs = useScheduleStore((s) => s.jobs);
  const refresh = useScheduleStore((s) => s.refresh);
  const create = useScheduleStore((s) => s.create);
  const remove = useScheduleStore((s) => s.remove);
  const setEnabled = useScheduleStore((s) => s.setEnabled);
  const runNow = useScheduleStore((s) => s.runNow);
  const loading = useScheduleStore((s) => s.loading);

  const [draftName, setDraftName] = useState("");
  const [draftCron, setDraftCron] = useState("");
  const [draftPrompt, setDraftPrompt] = useState("");

  useEffect(() => { if (jobs.length === 0) void refresh(); }, [jobs.length, refresh]);

  return (
    <section data-testid="settings-scheduled" className="space-y-4">
      <div>
        <h2 className="text-sm font-medium">Scheduled jobs</h2>
        <p className="mt-0.5 text-[11px] text-minimax-muted">
          Cron jobs the agent runs on a schedule. Use 5-field cron
          expressions (e.g. <code>*/5 * * * *</code> = every 5 minutes).
        </p>
      </div>
      <div className="rounded-md border border-minimax-border bg-minimax-panel/40 p-3">
        <div className="grid grid-cols-12 gap-2 text-xs">
          <input data-testid="settings-job-name" value={draftName}
            onChange={(e) => setDraftName(e.target.value)} placeholder="job name"
            className="col-span-3 rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-minimax-fg" />
          <input data-testid="settings-job-cron" value={draftCron}
            onChange={(e) => setDraftCron(e.target.value)} placeholder="cron expr"
            className="col-span-3 rounded border border-minimax-border bg-minimax-bg px-2 py-1 font-mono text-minimax-fg" />
          <input data-testid="settings-job-prompt" value={draftPrompt}
            onChange={(e) => setDraftPrompt(e.target.value)} placeholder="prompt payload"
            className="col-span-4 rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-minimax-fg" />
          <button type="button" data-testid="settings-job-add"
            disabled={!draftName.trim() || !draftCron.trim()}
            onClick={async () => {
              const job = await create({ name: draftName.trim(), cron: draftCron.trim(), prompt: draftPrompt.trim() });
              if (job) { toast.success("Job created", job.name); setDraftName(""); setDraftCron(""); setDraftPrompt(""); }
            }}
            className="col-span-2 inline-flex items-center justify-center gap-1 rounded border border-minimax-accent/40 bg-minimax-accent/10 px-2 py-1 text-xs text-minimax-accent hover:bg-minimax-accent/20 disabled:cursor-not-allowed disabled:opacity-50">
            <Save size={12} /> Create
          </button>
        </div>
      </div>
      <ul className="space-y-1.5" data-testid="settings-jobs-list">
        {jobs.length === 0 && !loading && (
          <li className="rounded border border-dashed border-minimax-border px-3 py-4 text-center text-xs text-minimax-muted">No scheduled jobs</li>
        )}
        {jobs.map((j) => (
          <ScheduledJobRow key={j.id} job={j}
            onToggle={(enabled) => void setEnabled(j.id, enabled)}
            onDelete={() => void remove(j.id)}
            onRunNow={() => void runNow(j.id)}
          />
        ))}
      </ul>
    </section>
  );
}

function ScheduledJobRow({ job, onToggle, onDelete, onRunNow }: {
  job: ScheduledJob; onToggle: (enabled: boolean) => void;
  onDelete: () => void; onRunNow: () => void;
}): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const tasks = useTaskStore((s) => s.tasks);

  const relatedTasks = useMemo(() => {
    const allTasks = Object.values(tasks);
    if (allTasks.length === 0) return [];
    const jobName = job.name.toLowerCase();
    return allTasks.filter((t) =>
      (t.message && t.message.toLowerCase().includes(jobName)) ||
      t.task_id.toLowerCase().includes(job.id.toLowerCase().slice(0, 6))
    );
  }, [tasks, job.name, job.id]);

  return (
    <li data-testid={`settings-job-row-${job.id}`} className="rounded-md border border-minimax-border bg-minimax-panel/40">
      <div className="flex items-center gap-2 px-3 py-2 text-sm">
        <button type="button" data-testid={`settings-job-expand-${job.id}`}
          onClick={() => setExpanded((v) => !v)}
          className="shrink-0 rounded p-0.5 text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
          aria-label={expanded ? "Collapse" : "Expand"}>
          {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </button>
        <span className="flex-1 min-w-0">
          <span className="block truncate font-medium text-minimax-fg">{job.name}</span>
          <span className="block truncate font-mono text-[11px] text-minimax-muted">{job.cron} · {job.prompt || "(no prompt)"}</span>
        </span>
        <label className="inline-flex cursor-pointer items-center gap-1.5 text-[11px] text-minimax-muted" data-testid={`settings-job-toggle-${job.id}`}>
          <input type="checkbox" checked={job.enabled} onChange={(e) => onToggle(e.target.checked)} className="h-3 w-3 accent-minimax-accent" />
          {job.enabled ? "enabled" : "disabled"}
        </label>
        <button type="button" data-testid={`settings-job-run-now-${job.id}`} onClick={onRunNow} aria-label="Run job now"
          className="rounded border border-minimax-border p-1 text-minimax-muted hover:text-emerald-300"><Play size={12} /></button>
        <button type="button" data-testid={`settings-job-delete-${job.id}`} onClick={onDelete} aria-label="Delete job"
          className="rounded border border-minimax-border p-1 text-minimax-muted hover:text-red-300"><Trash2 size={12} /></button>
      </div>
      {expanded && (
        <div data-testid={`settings-job-tasks-${job.id}`} className="border-t border-minimax-border/60 px-3 py-2">
          {relatedTasks.length === 0 ? (
            <div className="text-[11px] italic text-minimax-muted">No task runs recorded yet.</div>
          ) : (
            <ul className="space-y-1">
              {relatedTasks.map((t) => (
                <li key={t.task_id} className="flex items-center justify-between text-[11px]">
                  <div className="min-w-0 flex items-center gap-1.5">
                    <TaskStatusDot status={t.status} />
                    <span className="truncate text-minimax-fg">{t.message || t.task_id}</span>
                  </div>
                  <div className="flex shrink-0 items-center gap-2 text-minimax-muted">
                    <span>{Math.round(t.progress * 100)}%</span>
                    <span>{t.status === "running" ? "running" : new Date(t.updated_at).toLocaleTimeString()}</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </li>
  );
}

function TaskStatusDot({ status }: { status: string }): JSX.Element {
  const colors: Record<string, string> = {
    running: "bg-minimax-accent animate-pulse", done: "bg-emerald-400",
    error: "bg-red-400", cancelled: "bg-minimax-muted",
  };
  return <span aria-hidden className={`inline-block h-1.5 w-1.5 rounded-full ${colors[status] ?? "bg-minimax-muted"}`} />;
}

/**
 * Scheduled tab — manage cron jobs via `schedule.*` IPC.
 * Includes ScheduledJobRow and TaskStatusDot sub-components.
 */
import { useEffect, useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Play,
  Plus,
  Trash2,
} from "lucide-react";
import { Button, IconButton, Input, Panel } from "../../ui";
import { useScheduleStore, useTaskStore } from "../../stores";
import { toast } from "../layout/ErrorBoundary";
import type { ScheduledJob } from "../../types/ipc";
import { formatTime } from "../../lib/time";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { InlineCode, TabHeader } from "./fields";

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

  const handleCreate = async () => {
    const job = await create({ name: draftName.trim(), cron: draftCron.trim(), prompt: draftPrompt.trim() });
    if (job) {
      toast.success("Job created", job.name);
      setDraftName(""); setDraftCron(""); setDraftPrompt("");
    }
  };

  return (
    <section data-testid="settings-scheduled" className="space-y-4">
      <TabHeader
        title="Scheduled jobs"
        hint={
          <>
            Cron jobs the agent runs on a schedule. Use 5-field cron expressions (e.g.{" "}
            <InlineCode>*/5 * * * *</InlineCode> = every 5 minutes).
          </>
        }
      />

      <Panel title="New job">
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-12">
          <label htmlFor="scheduled-job-name" className="sr-only">Job Name</label>
          <Input
            id="scheduled-job-name"
            name="scheduled-job-name"
            autoComplete="off"
            data-testid="settings-job-name"
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            placeholder="e.g. Nightly review…"
            className="sm:col-span-3"
          />
          <label htmlFor="scheduled-job-cron" className="sr-only">Cron Expression</label>
          <Input
            id="scheduled-job-cron"
            name="scheduled-job-cron"
            autoComplete="off"
            spellCheck={false}
            data-testid="settings-job-cron"
            value={draftCron}
            onChange={(e) => setDraftCron(e.target.value)}
            placeholder="e.g. 0 2 * * *…"
            className="font-mono sm:col-span-3"
          />
          <label htmlFor="scheduled-job-prompt" className="sr-only">Prompt</label>
          <Input
            id="scheduled-job-prompt"
            name="scheduled-job-prompt"
            autoComplete="off"
            data-testid="settings-job-prompt"
            value={draftPrompt}
            onChange={(e) => setDraftPrompt(e.target.value)}
            placeholder="e.g. Review recent changes…"
            className="sm:col-span-4"
          />
          <Button
            size="sm"
            variant="subtle"
            data-testid="settings-job-add"
            disabled={!draftName.trim() || !draftCron.trim()}
            onClick={() => void handleCreate()}
            icon={<Plus />}
            className="sm:col-span-2"
          >
            Create
          </Button>
        </div>
      </Panel>

      <ul className="space-y-1.5" data-testid="settings-jobs-list">
        {jobs.length === 0 && !loading && (
          <li className="rounded-lg border border-dashed border-line px-3 py-4 text-center text-xs text-ink-2">
            No scheduled jobs
          </li>
        )}
        {jobs.map((j) => (
          <ScheduledJobRow
            key={j.id}
            job={j}
            onToggle={(enabled) => void setEnabled(j.id, enabled)}
            onDelete={async () => {
              const accepted = await requestConfirmation({
                title: `Delete scheduled job ${j.name}?`,
                description: "The job will stop running and its schedule will be permanently removed.",
                confirmLabel: "Delete Job",
              });
              if (accepted) await remove(j.id);
            }}
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
    <li
      data-testid={`settings-job-row-${job.id}`}
      className="rounded-lg border border-line bg-surface-2 transition-colors duration-150 hover:border-line-strong"
    >
      <div className="flex items-center gap-2 px-3 py-2 text-sm">
        <IconButton
          data-testid={`settings-job-expand-${job.id}`}
          onClick={() => setExpanded((v) => !v)}
          aria-label={expanded ? "Collapse" : "Expand"}
          active={expanded}
        >
          {expanded ? <ChevronDown /> : <ChevronRight />}
        </IconButton>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs font-medium text-ink-0">{job.name}</span>
          <span className="block truncate font-mono text-[11px] text-ink-2">
            {job.cron} · {job.prompt || "(no prompt)"}
          </span>
        </span>
        <label
          className="inline-flex cursor-pointer items-center gap-1.5 text-[11px] text-ink-2"
          data-testid={`settings-job-toggle-${job.id}`}
        >
          <input
            type="checkbox"
            checked={job.enabled}
            onChange={(e) => onToggle(e.target.checked)}
            className="h-3 w-3 accent-accent"
          />
          {job.enabled ? "enabled" : "disabled"}
        </label>
        <IconButton
          data-testid={`settings-job-run-now-${job.id}`}
          onClick={onRunNow}
          aria-label="Run job now"
        >
          <Play />
        </IconButton>
        <IconButton
          data-testid={`settings-job-delete-${job.id}`}
          onClick={onDelete}
          aria-label="Delete job"
        >
          <Trash2 />
        </IconButton>
      </div>
      {expanded && (
        <div data-testid={`settings-job-tasks-${job.id}`} className="border-t border-line px-3 py-2">
          {relatedTasks.length === 0 ? (
            <div className="text-[11px] italic text-ink-2">No task runs recorded yet.</div>
          ) : (
            <ul className="space-y-1">
              {relatedTasks.map((t) => (
                <li key={t.task_id} className="flex items-center justify-between gap-2 text-[11px]">
                  <div className="flex min-w-0 items-center gap-1.5">
                    <TaskStatusDot status={t.status} />
                    <span className="truncate text-ink-0">{t.message || t.task_id}</span>
                  </div>
                  <div className="flex shrink-0 items-center gap-2 text-ink-2">
                    <span>{Math.round(t.progress * 100)}%</span>
                    <span>{t.status === "running" ? "running" : formatTime(t.updated_at)}</span>
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
    running: "bg-accent animate-pulse", done: "bg-status-success",
    error: "bg-status-error", cancelled: "bg-ink-2",
  };
  return <span aria-hidden className={`inline-block h-1.5 w-1.5 rounded-full ${colors[status] ?? "bg-ink-2"}`} />;
}

/** Revision-based schedule editing preserves immutable occurrence history. */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ScheduledJob } from "../../types/ipc";

vi.mock("../../ipc", () => ({
  ipc: { agentBaseUrl: "local", on: vi.fn(() => () => {}), onSideCar: vi.fn(() => () => {}) },
  typedIPC: {
    listJobs: vi.fn(),
    createJob: vi.fn(),
    updateJob: vi.fn(),
    listScheduleOccurrences: vi.fn().mockResolvedValue({occurrences:[]}),
    deleteJob: vi.fn(),
    enableJob: vi.fn(),
    disableJob: vi.fn(),
    runNowJob: vi.fn(),
  },
}));

vi.mock("../../components/layout/ErrorBoundary", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import { ipc, typedIPC } from "../../ipc";
import { useScheduleStore, useTaskStore } from "../../stores";
import { ScheduledTab } from "./ScheduledTab";
import { ScheduleHistory } from "./ScheduleContextFields";
import * as confirmation from "../modals/ConfirmationDialog";

const listJobs = vi.mocked(typedIPC.listJobs);
const createJob = vi.mocked(typedIPC.createJob);
const deleteJob = vi.mocked(typedIPC.deleteJob);
const updateJob = vi.mocked(typedIPC.updateJob);

function makeJob(overrides: Partial<ScheduledJob> = {}): ScheduledJob {
  return {
    id: "job_1", revision:1,binding_state:"ready",
    context:{project_id:"p",provider_id:"offline",model:"test",budget_tokens:1000,mode:"execute",timezone:"Asia/Shanghai"},
    name: "Nightly Review",
    cron: "0 2 * * *",
    prompt: "审查最近的改动",
    enabled: true,
    last_run_at: null,
    next_run_at: null,
    ...overrides,
  };
}

describe("ScheduledTab editing (P2-5)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useScheduleStore.setState({ jobs: [], loading: false });
    useTaskStore.setState({ tasks: {} });
    listJobs.mockResolvedValue({ jobs: [makeJob()] });
  });

  it("prefills the edit form with the job's current definition", async () => {
    const user = userEvent.setup();
    render(<ScheduledTab />);
    await waitFor(() => {
      expect(screen.getByTestId("settings-job-row-job_1")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("settings-job-edit-job_1"));
    expect(screen.getByTestId("settings-job-edit-form-job_1")).toBeInTheDocument();
    expect(screen.getByTestId("settings-job-edit-name")).toHaveValue("Nightly Review");
    expect(screen.getByTestId("settings-job-edit-cron")).toHaveValue("0 2 * * *");
    expect(screen.getByTestId("settings-job-edit-prompt")).toHaveValue("审查最近的改动");
  });

  it("saves the same job with a revision compare-and-swap", async () => {
    const user = userEvent.setup();
    const updated = makeJob({ id: "job_1", revision:2, name: "Morning Review", cron: "30 7 * * *" });
    updateJob.mockResolvedValue({ job: updated });
    deleteJob.mockResolvedValue({ ok: true });

    render(<ScheduledTab />);
    await waitFor(() => {
      expect(screen.getByTestId("settings-job-row-job_1")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("settings-job-edit-job_1"));
    fireEvent.change(screen.getByTestId("settings-job-edit-name"), {
      target: { value: "Morning Review" },
    });
    fireEvent.change(screen.getByTestId("settings-job-edit-cron"), {
      target: { value: "30 7 * * *" },
    });
    await user.click(screen.getByTestId("settings-job-edit-save"));

    await waitFor(() => {
      expect(updateJob).toHaveBeenCalledWith("job_1",1,{
        name: "Morning Review",
        cron: "30 7 * * *",
        prompt: "审查最近的改动", context: makeJob().context,
      });
      expect(deleteJob).not.toHaveBeenCalled();
    });
    // The list reflects the replacement, and the inline form closes.
    await waitFor(() => {
      expect(screen.getByText("Morning Review")).toBeInTheDocument();
      expect(screen.queryByTestId("settings-job-edit-form-job_1")).not.toBeInTheDocument();
    });
  });

  it("cancel leaves the job untouched — no create, no delete", async () => {
    const user = userEvent.setup();
    render(<ScheduledTab />);
    await waitFor(() => {
      expect(screen.getByTestId("settings-job-row-job_1")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("settings-job-edit-job_1"));
    fireEvent.change(screen.getByTestId("settings-job-edit-name"), {
      target: { value: "Discarded" },
    });
    await user.click(screen.getByTestId("settings-job-edit-cancel"));

    expect(screen.queryByTestId("settings-job-edit-form-job_1")).not.toBeInTheDocument();
    expect(createJob).not.toHaveBeenCalled();
    expect(deleteJob).not.toHaveBeenCalled();
    expect(screen.getByText("Nightly Review")).toBeInTheDocument();
  });

  it("reopening after a cancelled edit resets the draft to the job's values", async () => {
    const user = userEvent.setup();
    render(<ScheduledTab />);
    await waitFor(() => {
      expect(screen.getByTestId("settings-job-row-job_1")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("settings-job-edit-job_1"));
    fireEvent.change(screen.getByTestId("settings-job-edit-name"), {
      target: { value: "Discarded" },
    });
    await user.click(screen.getByTestId("settings-job-edit-cancel"));
    // Reopen: the stale edit must not leak back into the form.
    await user.click(screen.getByTestId("settings-job-edit-job_1"));
    expect(screen.getByTestId("settings-job-edit-name")).toHaveValue("Nightly Review");
  });
});


it("retains the manual key AND original revision after lost ACK and an edit", async () => {
  useScheduleStore.setState({jobs:[makeJob({id:"retry"})]});
  const run = vi.mocked(typedIPC.runNowJob);
  run.mockRejectedValueOnce(new Error("lost ACK"));
  await useScheduleStore.getState().runNow("retry");
  const original = run.mock.calls.at(-1)!;
  useScheduleStore.setState({jobs:[makeJob({id:"retry",revision:2})]});
  run.mockResolvedValueOnce({ok:true,job_id:"retry",occurrence_id:"o",task_id:"t",run_id:"r",status:"completed",created:false});
  await useScheduleStore.getState().runNow("retry");
  expect(run.mock.calls.at(-1)).toEqual(original);
  useScheduleStore.setState({jobs:[makeJob({id:"retry",revision:2})]});
  await useScheduleStore.getState().runNow("retry");
  expect(run.mock.calls.at(-1)?.[1]).not.toBe(original[1]);
  expect(run.mock.calls.at(-1)?.[2]).toBe(2);
});

it("refreshes a collapsed occurrence when it starts waiting for approval", async () => {
  const events = new Map<string, () => void>();
  vi.mocked(ipc.on).mockImplementation((event, callback) => { events.set(event, () => callback({} as never)); return () => {}; });
  const occurrence = { id: "occ", job_id: "job_1", revision: 1, source: "cron" as const, session_id: "session",
    task_id: "task", run_id: "run", status: "running", execution_status: "running", created_at: "now",
    context: makeJob().context! };
  vi.mocked(typedIPC.listScheduleOccurrences).mockResolvedValueOnce({ occurrences: [occurrence] })
    .mockResolvedValueOnce({ occurrences: [{ ...occurrence, execution_status: "awaiting_approval" }] });
  render(<ScheduleHistory job={makeJob({ active_occurrence_id: "occ" })} expanded={false} />);
  expect(await screen.findByText(/当前发生：运行中/)).toBeVisible();
  events.get("run.updated")?.();
  expect(await screen.findByText(/当前发生：等待授权/)).toBeVisible();
  expect(screen.getByRole("button", { name: "打开会话处理授权" })).toBeVisible();
});

it("keeps a new schedule draft when the creation form is collapsed", async () => {
  const user = userEvent.setup();
  render(<ScheduledTab />);
  const summary = screen.getByText("新建任务", { selector: "summary" });
  await user.click(summary);
  const name = screen.getByTestId("settings-job-name");
  await user.type(name, "保留的草稿");
  await user.click(screen.getByText(/新建任务.*草稿已保留/, { selector: "summary" }));
  expect(name).toHaveValue("保留的草稿");
  await user.click(screen.getByText(/新建任务.*草稿已保留/, { selector: "summary" }));
  expect(name).toBeVisible();
  expect(name).toHaveValue("保留的草稿");
});


it("opens and focuses the restored draft after abandoning an uncertain create", async () => {
  const user = userEvent.setup();
  const key = `minimax-code:schedule-create:${JSON.stringify(["local", "svc"])}`;
  sessionStorage.setItem(key, JSON.stringify({ name: "恢复计划", cron: "0 2 * * *", prompt: "内容", context: makeJob().context,
    enabled: false, expected_service_id: "svc", idempotency_key: "original-key" }));
  const confirm = vi.spyOn(confirmation, "requestConfirmation").mockResolvedValue(true);
  try {
    render(<ScheduledTab serviceId="svc" />);
    await user.click(screen.getByRole("button", { name: "放弃请求并编辑草稿" }));
    const input = screen.getByTestId("settings-job-name");
    expect(input).toBeVisible();
    expect(input).toHaveValue("恢复计划");
    await waitFor(() => expect(input).toHaveFocus());
  } finally { confirm.mockRestore(); sessionStorage.removeItem(key); }
});

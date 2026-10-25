/** Revision-based schedule editing preserves immutable occurrence history. */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ScheduledJob } from "../../types/ipc";

vi.mock("../../ipc", () => ({
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

import { typedIPC } from "../../ipc";
import { useScheduleStore, useTaskStore } from "../../stores";
import { ScheduledTab } from "./ScheduledTab";

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

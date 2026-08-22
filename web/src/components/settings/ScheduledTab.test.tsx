/**
 * ScheduledTab edit flow (P2-5) — the pencil button opens an inline form
 * prefilled with the job's current definition; saving composes
 * create(new) + delete(old) in the store because the wire contract has
 * no schedule.update.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ScheduledJob } from "../../types/ipc";

vi.mock("../../ipc", () => ({
  typedIPC: {
    listJobs: vi.fn(),
    createJob: vi.fn(),
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

function makeJob(overrides: Partial<ScheduledJob> = {}): ScheduledJob {
  return {
    id: "job_1",
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

  it("saves by creating the new job then deleting the old one", async () => {
    const user = userEvent.setup();
    const updated = makeJob({ id: "job_2", name: "Morning Review", cron: "30 7 * * *" });
    createJob.mockResolvedValue({ job: updated });
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
      expect(createJob).toHaveBeenCalledWith({
        name: "Morning Review",
        cron: "30 7 * * *",
        prompt: "审查最近的改动",
      });
      expect(deleteJob).toHaveBeenCalledWith("job_1");
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

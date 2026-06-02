/**
 * Settings page tests — verifies the three tabs render, model
 * switching round-trips through the typed IPC, permission rules
 * can be added/deleted, and scheduled jobs can be toggled.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SettingsPage } from "../src/components/SettingsPage";
import {
  useModelStore,
  usePermissionStore,
  useScheduleStore,
} from "../src/stores";

// Mock the typed IPC so we can drive each namespace independently.
vi.mock("../src/ipc", async () => {
  const actual = await vi.importActual<typeof import("../src/ipc")>("../src/ipc");
  return {
    ...actual,
    typedIPC: {
      ...actual.typedIPC,
      listModels: vi.fn(async () => ({
        models: [
          { id: "m1", name: "Alpha", provider: "MiniMax", context_window: 8000, supports_tools: true },
          { id: "m2", name: "Beta", provider: "MiniMax", context_window: 32000, supports_tools: true },
        ],
        current: "m1",
      })),
      setCurrentModel: vi.fn(async (modelId: string) => ({ current: modelId })),
      listRules: vi.fn(async () => ({ rules: [] })),
      setRule: vi.fn(
        async (rule: { id?: string; tool: string; pattern: string; decision: "allow" | "deny" | "ask" }) => ({
          rule: {
            id: rule.id ?? `rule_${Math.random().toString(36).slice(2, 6)}`,
            tool: rule.tool,
            pattern: rule.pattern,
            decision: rule.decision,
            created_at: Date.now(),
          },
        }),
      ),
      deleteRule: vi.fn(async () => ({ ok: true })),
      listJobs: vi.fn(async () => ({ jobs: [] })),
      createJob: vi.fn(
        async (opts: { name: string; cron: string; prompt: string }) => ({
          job: {
            id: `job_${Math.random().toString(36).slice(2, 6)}`,
            name: opts.name,
            cron: opts.cron,
            prompt: opts.prompt,
            enabled: true,
            last_run_at: null,
            next_run_at: null,
          },
        }),
      ),
      deleteJob: vi.fn(async () => ({ ok: true })),
      enableJob: vi.fn(async (jobId: string) => ({
        job: {
          id: jobId,
          name: "stub",
          cron: "* * * * *",
          prompt: "stub",
          enabled: true,
          last_run_at: null,
          next_run_at: null,
        },
      })),
      disableJob: vi.fn(async (jobId: string) => ({
        job: {
          id: jobId,
          name: "stub",
          cron: "* * * * *",
          prompt: "stub",
          enabled: false,
          last_run_at: null,
          next_run_at: null,
        },
      })),
    },
  };
});

beforeEach(() => {
  useModelStore.setState({ models: [], current: null, loading: false });
  usePermissionStore.setState({ rules: [], alwaysAllow: false, loading: false });
  useScheduleStore.setState({ jobs: [], loading: false });
});

describe("SettingsPage", () => {
  it("renders with the models tab open by default", async () => {
    render(<SettingsPage />);
    await waitFor(() => {
      expect(screen.getByTestId("settings-models-list").children.length).toBe(2);
    });
    expect(screen.getByTestId("settings-models")).toBeInTheDocument();
    expect(screen.getByTestId("settings-title")).toHaveTextContent("Settings");
  });

  it("highlights the current model and disables its select button", async () => {
    render(<SettingsPage />);
    await waitFor(() => {
      expect(useModelStore.getState().models.length).toBe(2);
    });
    expect(screen.getByTestId("settings-model-current-m1")).toBeInTheDocument();
    const selectCurrent = screen.getByTestId("settings-model-select-m1");
    expect(selectCurrent).toBeDisabled();
  });

  it("switches model when the user clicks 'Use' on a non-current row", async () => {
    const { typedIPC } = await import("../src/ipc");
    render(<SettingsPage />);
    await waitFor(() => {
      expect(screen.getByTestId("settings-model-select-m2")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("settings-model-select-m2"));
    await waitFor(() => {
      expect(typedIPC.setCurrentModel).toHaveBeenCalledWith("m2");
    });
    await waitFor(() => {
      expect(useModelStore.getState().current).toBe("m2");
    });
  });

  it("switches to the permissions tab and shows an empty state", async () => {
    render(<SettingsPage />);
    fireEvent.click(screen.getByTestId("settings-tab-permissions"));
    expect(screen.getByTestId("settings-permissions")).toBeInTheDocument();
    expect(screen.getByTestId("settings-permissions-list").textContent).toMatch(
      /No permission rules yet/,
    );
  });

  it("adds a permission rule via the form", async () => {
    const { typedIPC } = await import("../src/ipc");
    render(<SettingsPage />);
    fireEvent.click(screen.getByTestId("settings-tab-permissions"));
    fireEvent.change(screen.getByTestId("settings-permission-pattern"), {
      target: { value: "^rm" },
    });
    fireEvent.click(screen.getByTestId("settings-permission-add"));
    await waitFor(() => {
      expect(typedIPC.setRule).toHaveBeenCalledWith(
        expect.objectContaining({ pattern: "^rm", decision: "allow" }),
      );
    });
    await waitFor(() => {
      expect(usePermissionStore.getState().rules.length).toBe(1);
    });
  });

  it("switches to the scheduled tab and creates a new job", async () => {
    const { typedIPC } = await import("../src/ipc");
    render(<SettingsPage />);
    fireEvent.click(screen.getByTestId("settings-tab-scheduled"));
    fireEvent.change(screen.getByTestId("settings-job-name"), {
      target: { value: "nightly" },
    });
    fireEvent.change(screen.getByTestId("settings-job-cron"), {
      target: { value: "0 2 * * *" },
    });
    fireEvent.click(screen.getByTestId("settings-job-add"));
    await waitFor(() => {
      expect(typedIPC.createJob).toHaveBeenCalledWith({
        name: "nightly",
        cron: "0 2 * * *",
        prompt: "",
      });
    });
    await waitFor(() => {
      expect(useScheduleStore.getState().jobs.length).toBe(1);
    });
  });

  it("toggles a job's enabled state", async () => {
    useScheduleStore.setState({
      jobs: [
        {
          id: "job_1",
          name: "echo",
          cron: "* * * * *",
          prompt: "hi",
          enabled: true,
          last_run_at: null,
          next_run_at: null,
        },
      ],
    });
    const { typedIPC } = await import("../src/ipc");
    render(<SettingsPage />);
    fireEvent.click(screen.getByTestId("settings-tab-scheduled"));
    const checkbox = screen.getByTestId("settings-job-toggle-job_1").querySelector(
      'input[type="checkbox"]',
    ) as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
    fireEvent.click(checkbox);
    await waitFor(() => {
      expect(typedIPC.disableJob).toHaveBeenCalledWith("job_1");
    });
  });
});

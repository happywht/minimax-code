/**
 * Tests for the WorkflowsTab step editor (P1-2) — expand, edit config,
 * add/remove steps, and the dirty-gated save flow.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// vi.mock is hoisted — factory must be self-contained, no external refs
vi.mock("../src/stores", () => {
  /** Mock Zustand store: supports both selector and direct-call patterns. */
  const ms = (state: Record<string, unknown>) => {
    const fn = (sel?: (s: Record<string, unknown>) => unknown) =>
      sel ? sel(state) : state;
    return vi.fn(fn);
  };
  return {
    useWorkflowStore: ms({
      entries: [],
      total: 0,
      loading: false,
      error: null,
      refresh: vi.fn(),
      create: vi.fn(),
      update: vi.fn().mockResolvedValue(undefined),
      remove: vi.fn(),
      enable: vi.fn(),
      disable: vi.fn(),
      trigger: vi.fn(),
    }),
  };
});

vi.mock("../src/components/modals/ConfirmationDialog", () => ({
  requestConfirmation: vi.fn(async () => true),
}));
vi.mock("../src/components/layout/ErrorBoundary", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import { WorkflowsTab } from "../src/components/settings/WorkflowsTab";
import { useWorkflowStore } from "../src/stores";

const WF = {
  id: "wf_1",
  name: "PR 审查",
  description: "",
  enabled: true,
  trigger_type: "webhook" as const,
  trigger_config: {},
  steps: [
    {
      type: "condition" as const,
      if: { field: "action", op: "eq" as const, value: "opened" },
      then_step: 1,
      else_step: -1,
    },
    { type: "action" as const, action_type: "run-skill" as const, config: { skill_id: "code-review" } },
  ],
  last_run_at: null,
  run_count: 0,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

const store = useWorkflowStore as unknown as ReturnType<typeof vi.fn> & {
  (sel?: (s: Record<string, unknown>) => unknown): unknown;
};

describe("WorkflowsTab step editor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    const state = store() as { entries: unknown[] };
    state.entries = [WF];
  });

  it("expands the step editor from the per-row button", () => {
    render(<WorkflowsTab />);
    expect(screen.queryByTestId("workflow-steps-wf_1")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("workflow-edit-steps-wf_1"));
    expect(screen.getByTestId("workflow-steps-wf_1")).toBeInTheDocument();
    // Both fixture steps render with their own testids.
    expect(screen.getByTestId("workflow-step-wf_1-0")).toBeInTheDocument();
    expect(screen.getByTestId("workflow-step-wf_1-1")).toBeInTheDocument();
  });

  it("save stays disabled until a step is edited, then submits the new steps", async () => {
    render(<WorkflowsTab />);
    fireEvent.click(screen.getByTestId("workflow-edit-steps-wf_1"));

    const save = screen.getByTestId("workflow-steps-save-wf_1") as HTMLButtonElement;
    expect(save.disabled).toBe(true);

    const skillInput = screen.getByTestId("workflow-step-cfg-wf_1-1-skill") as HTMLInputElement;
    fireEvent.change(skillInput, { target: { value: "security-audit" } });
    expect(save.disabled).toBe(false);

    fireEvent.click(save);
    const state = store() as { update: (id: string, fields: unknown) => Promise<void> };
    await waitFor(() => expect(state.update).toHaveBeenCalled());
    const [id, fields] = (state.update as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(id).toBe("wf_1");
    const steps = (fields as { steps: Array<{ config?: { skill_id?: string } }> }).steps;
    expect(steps[1].config?.skill_id).toBe("security-audit");
  });

  it("adds an action step, and a net-zero add+delete re-disables save", async () => {
    render(<WorkflowsTab />);
    fireEvent.click(screen.getByTestId("workflow-edit-steps-wf_1"));
    const save = screen.getByTestId("workflow-steps-save-wf_1") as HTMLButtonElement;

    fireEvent.click(screen.getByTestId("workflow-step-add-action-wf_1"));
    expect(screen.getByTestId("workflow-step-wf_1-2")).toBeInTheDocument();
    expect(save.disabled).toBe(false);

    // Three delete buttons exist (one per step); the last one is the
    // freshly added step at index 2. Removing it makes the list identical
    // to the persisted steps again — save must re-disable (net-zero edit).
    const deleteButtons = screen.getAllByLabelText("删除此步骤");
    expect(deleteButtons).toHaveLength(3);
    fireEvent.click(deleteButtons[2]);
    expect(screen.queryByTestId("workflow-step-wf_1-2")).not.toBeInTheDocument();
    expect(save.disabled).toBe(true);

    // Deleting a persisted step makes it dirty again and submits 1 step.
    const twoButtons = screen.getAllByLabelText("删除此步骤");
    fireEvent.click(twoButtons[1]);
    fireEvent.click(save);
    const state = store() as { update: (id: string, fields: unknown) => Promise<void> };
    await waitFor(() => expect(state.update).toHaveBeenCalled());
    const [, fields] = (state.update as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect((fields as { steps: unknown[] }).steps).toHaveLength(1);
  });

  it("edits a condition's comparison value", async () => {
    render(<WorkflowsTab />);
    fireEvent.click(screen.getByTestId("workflow-edit-steps-wf_1"));

    const valueInput = screen.getByTestId("workflow-step-cond-wf_1-0-value") as HTMLInputElement;
    fireEvent.change(valueInput, { target: { value: "reopened" } });
    fireEvent.click(screen.getByTestId("workflow-steps-save-wf_1"));

    const state = store() as { update: (id: string, fields: unknown) => Promise<void> };
    await waitFor(() => expect(state.update).toHaveBeenCalled());
    const [, fields] = (state.update as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    const cond = (fields as { steps: Array<{ if?: { value?: string } }> }).steps[0].if;
    expect(cond?.value).toBe("reopened");
  });
});

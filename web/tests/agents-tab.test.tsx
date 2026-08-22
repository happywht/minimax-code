/**
 * Tests for the AgentsTab inline edit + enable/disable flows (P1-3).
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../src/stores", () => {
  /** Mock Zustand store: supports both selector and direct-call patterns. */
  const ms = (state: Record<string, unknown>) => {
    const fn = (sel?: (s: Record<string, unknown>) => unknown) =>
      sel ? sel(state) : state;
    return vi.fn(fn);
  };
  return {
    useAgentStore: ms({
      agents: [],
      loading: false,
      refresh: vi.fn(),
      create: vi.fn(),
      update: vi.fn().mockResolvedValue(undefined),
      remove: vi.fn(),
    }),
  };
});

vi.mock("../src/components/modals/ConfirmationDialog", () => ({
  requestConfirmation: vi.fn(async () => true),
}));
vi.mock("../src/components/layout/ErrorBoundary", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import { AgentsTab } from "../src/components/settings/AgentsTab";
import { useAgentStore } from "../src/stores";

const AGENT = {
  id: "agent_1",
  name: "code-reviewer",
  description: "审查代码",
  enabled: true,
  system_prompt: "你是一个代码审查员",
  model: "abab6.5s-chat",
};

const store = useAgentStore as unknown as ReturnType<typeof vi.fn> & {
  (sel?: (s: Record<string, unknown>) => unknown): unknown;
};

describe("AgentsTab edit & toggle (P1-3)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    const state = store() as { agents: unknown[] };
    state.agents = [AGENT];
  });

  it("toggles an agent's enabled state via the row button", () => {
    render(<AgentsTab />);
    fireEvent.click(screen.getByTestId("settings-agent-toggle-code-reviewer"));
    const state = store() as { update: (opts: unknown) => Promise<void> };
    expect(state.update).toHaveBeenCalledWith({
      name: "code-reviewer",
      enabled: false,
    });
  });

  it("opens the edit form, gates save on dirty, and submits the new prompt", async () => {
    render(<AgentsTab />);
    fireEvent.click(screen.getByTestId("settings-agent-edit-code-reviewer"));

    const form = screen.getByTestId("settings-agent-editform-code-reviewer");
    expect(form).toBeInTheDocument();

    const prompt = screen.getByTestId(
      "settings-agent-edit-prompt-code-reviewer",
    ) as HTMLTextAreaElement;
    const submit = screen.getByTestId(
      "settings-agent-edit-submit-code-reviewer",
    ) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);

    fireEvent.change(prompt, { target: { value: "你是一个安全审查员" } });
    expect(submit.disabled).toBe(false);
    fireEvent.click(submit);

    const state = store() as { update: (opts: unknown) => Promise<void> };
    await waitFor(() => expect(state.update).toHaveBeenCalled());
    expect(state.update).toHaveBeenCalledWith({
      name: "code-reviewer",
      system_prompt: "你是一个安全审查员",
      model: "abab6.5s-chat",
    });
  });

  it("shows the model field and lets it be cleared", async () => {
    render(<AgentsTab />);
    fireEvent.click(screen.getByTestId("settings-agent-edit-code-reviewer"));

    const model = screen.getByTestId(
      "settings-agent-edit-model-code-reviewer",
    ) as HTMLInputElement;
    expect(model.value).toBe("abab6.5s-chat");
    fireEvent.change(model, { target: { value: "" } });
    fireEvent.click(screen.getByTestId("settings-agent-edit-submit-code-reviewer"));

    const state = store() as { update: (opts: unknown) => Promise<void> };
    await waitFor(() => expect(state.update).toHaveBeenCalled());
    expect(state.update).toHaveBeenCalledWith({
      name: "code-reviewer",
      system_prompt: "你是一个代码审查员",
      model: undefined,
    });
  });
});

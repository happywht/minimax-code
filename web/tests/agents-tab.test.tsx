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
      executionProfiles: [],
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
import type { ExecutionProfileCapability } from "../src/types/ipc";

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
    const state = store() as { agents: unknown[]; executionProfiles: ExecutionProfileCapability[] };
    state.agents = [AGENT];
    state.executionProfiles = [];
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
      skills: [],
      execution_profile_id: null,
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
      skills: [],
      execution_profile_id: null,
    });
  });
  it("edits ordered skill IDs and explicitly clears the dependency list", async () => {
    (store() as { agents:unknown[] }).agents = [{...AGENT,skills:["old:skill"]}];
    render(<AgentsTab />);
    fireEvent.click(screen.getByTestId("settings-agent-edit-code-reviewer"));
    const input = screen.getByTestId("settings-agent-edit-skills-code-reviewer");
    fireEvent.change(input, { target:{value:"pack:one\npack:two"} });
    fireEvent.click(screen.getByTestId("settings-agent-edit-submit-code-reviewer"));
    const state = store() as { update: ReturnType<typeof vi.fn> };
    expect(state.update).toHaveBeenLastCalledWith(expect.objectContaining({ skills:["pack:one","pack:two"] }));
    fireEvent.click(screen.getByTestId("settings-agent-edit-code-reviewer"));
    fireEvent.change(screen.getByTestId("settings-agent-edit-skills-code-reviewer"), { target:{value:""} });
    fireEvent.click(screen.getByTestId("settings-agent-edit-submit-code-reviewer"));
    expect(state.update).toHaveBeenLastCalledWith(expect.objectContaining({ skills:[] }));
  });
  it.each([
    { profile: "", expected: null, label: "default inherited scope" },
    { profile: "native-host", expected: "native-host", label: "explicit host scope" },
  ])("creates an Agent with ordered skills and $label", async ({ profile, expected }) => {
    const state = store() as { create: ReturnType<typeof vi.fn> };
    state.create.mockResolvedValue(AGENT);
    render(<AgentsTab />);
    fireEvent.click(screen.getByTestId("settings-agent-create"));
    fireEvent.change(screen.getByTestId("settings-agent-form-name"), { target:{value:"with-skills"} });
    fireEvent.change(screen.getByTestId("settings-agent-form-prompt"), { target:{value:"Agent"} });
    fireEvent.change(screen.getByTestId("settings-agent-form-skills"), { target:{value:"pack:one, pack:two"} });
    const selector = screen.getByLabelText("执行环境");
    expect(selector).toHaveValue("");
    expect(screen.getByRole("option", { name: "默认（根任务使用宿主，子任务继承父级）" })).toBeEnabled();
    expect(screen.getByRole("option", { name: "Native 宿主" })).toBeEnabled();
    if (profile) fireEvent.change(selector, { target: { value: profile } });
    fireEvent.click(screen.getByTestId("settings-agent-form-submit"));
    await waitFor(() => expect(state.create).toHaveBeenCalledWith({
      name: "with-skills", system_prompt: "Agent", skills: ["pack:one", "pack:two"],
      execution_profile_id: expected,
    }));
  });
  it("preserves a saved unavailable execution profile when editing the prompt", async () => {
    const state = store() as {
      agents: unknown[]; executionProfiles: ExecutionProfileCapability[]; update: ReturnType<typeof vi.fn>;
    };
    state.agents = [{ ...AGENT, execution_profile_id: "linux-podman-l1" }];
    state.executionProfiles = [{
      id: "linux-podman-l1", label: "Linux Podman L1", mode: "podman", available: false,
      reason: "Linux runtime unavailable", unattended_supported: false,
      boundary_summary: "Rootless container boundary",
    }];
    render(<AgentsTab />);
    fireEvent.click(screen.getByTestId("settings-agent-edit-code-reviewer"));
    expect(screen.getByLabelText("执行环境")).toHaveValue("linux-podman-l1");
    expect(screen.getByRole("option", { name: "Linux Podman L1（不可用）" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Linux runtime unavailable");
    fireEvent.change(screen.getByTestId("settings-agent-edit-prompt-code-reviewer"), {
      target: { value: "Updated prompt" },
    });
    fireEvent.click(screen.getByTestId("settings-agent-edit-submit-code-reviewer"));
    await waitFor(() => expect(state.update).toHaveBeenCalledWith({
      name: "code-reviewer", system_prompt: "Updated prompt", model: "abab6.5s-chat",
      skills: [], execution_profile_id: "linux-podman-l1",
    }));
  });
});

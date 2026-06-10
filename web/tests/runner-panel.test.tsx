import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RunnerPanel } from "../src/components/RunnerPanel";
import { useRunnerStore, useSessionStore, useTerminalStore } from "../src/stores";
import type { RunnerInfo, TerminalSession } from "../src/types/ipc";

const runnerMocks = vi.hoisted(() => {
  const runners: RunnerInfo[] = [
    {
      id: "native",
      label: "Native shell",
      kind: "native",
      available: true,
      command: null,
      version: null,
      reason: null,
      supports_prompt: false,
      supports_terminal: true,
    },
    {
      id: "codex-cli",
      label: "Codex CLI",
      kind: "external_cli",
      available: false,
      command: null,
      version: null,
      reason: "codex executable not found on PATH",
      supports_prompt: true,
      supports_terminal: true,
    },
    {
      id: "claude-code-cli",
      label: "Claude Code CLI",
      kind: "external_cli",
      available: true,
      command: "claude",
      version: "2.1.168",
      reason: null,
      supports_prompt: true,
      supports_terminal: true,
    },
  ];
  const session: TerminalSession = {
    id: "term_runner",
    command: "pnpm test",
    cwd: "D:/repo",
    session_id: "ses_current",
    run_id: "run_runner",
    status: "completed",
    started_at: 1,
    updated_at: 2,
    completed_at: 2,
    exit_code: 0,
    error: null,
    next_seq: 2,
  };
  return {
    runners,
    session,
    listRunners: vi.fn(),
    startRunner: vi.fn(),
    readTerminal: vi.fn(),
  };
});

vi.mock("../src/ipc", () => ({
  typedIPC: {
    listRunners: runnerMocks.listRunners,
    startRunner: runnerMocks.startRunner,
    readTerminal: runnerMocks.readTerminal,
  },
}));

describe("RunnerPanel", () => {
  beforeEach(() => {
    useRunnerStore.getState().reset();
    useTerminalStore.getState().reset();
    useSessionStore.setState({ currentSessionId: "ses_current" });
    runnerMocks.listRunners.mockReset().mockResolvedValue({ runners: runnerMocks.runners });
    runnerMocks.startRunner.mockReset().mockResolvedValue({
      runner: runnerMocks.runners[0],
      session: runnerMocks.session,
    });
    runnerMocks.readTerminal.mockReset().mockResolvedValue({
      session: runnerMocks.session,
      chunks: [{ seq: 1, stream: "stdout", text: "ok\n", received_at: 2 }],
    });
  });

  it("loads runners and starts the native runner for the current session", async () => {
    render(<RunnerPanel />);

    await waitFor(() => {
      expect(screen.getByTestId("runner-panel-runner-native")).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId("runner-panel-command"), {
      target: { value: "pnpm test" },
    });
    fireEvent.click(screen.getByTestId("runner-panel-start"));

    await waitFor(() => {
      expect(runnerMocks.startRunner).toHaveBeenCalledWith({
        runner_id: "native",
        command: "pnpm test",
        cwd: undefined,
        session_id: "ses_current",
        timeout_s: undefined,
      });
    });
    await waitFor(() => {
      expect(screen.getByTestId("runner-panel-last-start")).toHaveTextContent("term_runner");
    });
    expect(useTerminalStore.getState().activeId).toBe("term_runner");
  });

  it("shows external runner availability without enabling start", async () => {
    render(<RunnerPanel />);

    await waitFor(() => {
      expect(screen.getByTestId("runner-panel-runner-codex-cli")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("runner-panel-runner-codex-cli"));
    fireEvent.change(screen.getByTestId("runner-panel-command"), {
      target: { value: "hello" },
    });

    expect(screen.getByTestId("runner-panel-start")).toBeDisabled();
    expect(screen.getByText("codex executable not found on PATH")).toBeInTheDocument();
  });

  it("starts an available external runner with the current session", async () => {
    render(<RunnerPanel />);

    await waitFor(() => {
      expect(screen.getByTestId("runner-panel-runner-claude-code-cli")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("runner-panel-runner-claude-code-cli"));
    fireEvent.change(screen.getByTestId("runner-panel-command"), {
      target: { value: "summarize this repo" },
    });
    fireEvent.click(screen.getByTestId("runner-panel-start"));

    await waitFor(() => {
      expect(runnerMocks.startRunner).toHaveBeenCalledWith({
        runner_id: "claude-code-cli",
        command: "summarize this repo",
        cwd: undefined,
        session_id: "ses_current",
        timeout_s: undefined,
      });
    });
  });
});

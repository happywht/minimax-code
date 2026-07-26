import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TerminalPanel } from "../src/components/right-panel/TerminalPanel";
import { useSessionStore, useTerminalStore } from "../src/stores";

const terminalMocks = vi.hoisted(() => ({
  session: {
    id: "term_1",
    command: "pnpm test",
    cwd: "D:/repo",
    session_id: "ses_current",
    run_id: "run_terminal",
    status: "completed" as const,
    started_at: 1,
    updated_at: 2,
    completed_at: 2,
    exit_code: 0,
    error: null,
    next_seq: 2,
  },
  listTerminals: vi.fn(),
  startTerminal: vi.fn(),
  readTerminal: vi.fn(),
  stopTerminal: vi.fn(),
}));

vi.mock("../src/ipc", () => ({
  typedIPC: {
    listTerminals: terminalMocks.listTerminals,
    startTerminal: terminalMocks.startTerminal,
    readTerminal: terminalMocks.readTerminal,
    stopTerminal: terminalMocks.stopTerminal,
  },
}));

describe("TerminalPanel", () => {
  beforeEach(() => {
    useTerminalStore.getState().reset();
    useSessionStore.setState({ currentSessionId: "ses_current" });
    terminalMocks.listTerminals.mockReset().mockResolvedValue({ sessions: [] });
    terminalMocks.startTerminal.mockReset().mockResolvedValue({ session: terminalMocks.session });
    terminalMocks.readTerminal.mockReset().mockResolvedValue({
      session: terminalMocks.session,
      chunks: [{ seq: 1, stream: "stdout", text: "ok\n", received_at: 2 }],
    });
    terminalMocks.stopTerminal.mockReset().mockResolvedValue({
      session: { ...terminalMocks.session, status: "cancelled", error: "stopped by user" },
    });
  });

  it("starts a command and renders terminal output", async () => {
    render(<TerminalPanel />);

    fireEvent.change(screen.getByTestId("terminal-panel-command"), {
      target: { value: "pnpm test" },
    });
    await waitFor(() => {
      expect(screen.getByTestId("terminal-panel-run")).not.toBeDisabled();
    });
    fireEvent.click(screen.getByTestId("terminal-panel-run"));

    await waitFor(() => {
      expect(terminalMocks.startTerminal).toHaveBeenCalledWith({
        command: "pnpm test",
        cwd: undefined,
        session_id: "ses_current",
      });
    });
    await waitFor(() => {
      expect(screen.getByTestId("terminal-panel-output")).toHaveTextContent("ok");
    });
  });

  it("stops the active running session", async () => {
    useTerminalStore.setState({
      sessions: { term_1: { ...terminalMocks.session, status: "running", completed_at: null, exit_code: null } },
      order: ["term_1"],
      activeId: "term_1",
      chunks: { term_1: [] },
      lastSeq: { term_1: 0 },
    });
    render(<TerminalPanel />);

    fireEvent.click(screen.getByTestId("terminal-panel-stop"));

    await waitFor(() => {
      expect(terminalMocks.stopTerminal).toHaveBeenCalledWith("term_1");
    });
  });
});

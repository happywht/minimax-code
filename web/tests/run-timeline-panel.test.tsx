import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RunTimelinePanel } from "../src/components/RunTimelinePanel";
import { usePermissionStore, useRunTimelineStore } from "../src/stores";

vi.mock("../src/ipc", () => ({
  ipc: {
    on: vi.fn(() => vi.fn()),
  },
  typedIPC: {},
}));

describe("RunTimelinePanel", () => {
  beforeEach(() => {
    useRunTimelineStore.getState().reset();
    usePermissionStore.setState({ pending: {} });
  });

  it("renders run steps in timeline order", () => {
    useRunTimelineStore.setState({
      initialized: true,
      order: ["run_1"],
      runs: {
        run_1: {
          id: "run_1",
          session_id: "ses_1",
          mode: "chat",
          status: "running",
          title: "Fix tests",
          created_at: "2026-06-09T00:00:00Z",
          steps: [
            {
              id: "step_1",
              run_id: "run_1",
              session_id: "ses_1",
              kind: "tool_call",
              status: "completed",
              title: "exec_command",
              summary: '{"cmd":["pytest"]}',
              started_at: "2026-06-09T00:00:00Z",
              ordinal: 1,
            },
          ],
        },
      },
    });

    render(<RunTimelinePanel />);

    expect(screen.getByText("Fix tests")).toBeInTheDocument();
    expect(screen.getByText("exec_command")).toBeInTheDocument();
    expect(screen.getByText('{"cmd":["pytest"]}')).toBeInTheDocument();
  });

  it("renders patch preview for edit approvals", () => {
    usePermissionStore.setState({
      pending: {
        perm_1: {
          request_id: "perm_1",
          tool: "edit_file",
          args: {
            path: "src/app.ts",
            old_string: "const value = 1;\n",
            new_string: "const value = 2;\n",
          },
          received_at: 1,
        },
      },
    });

    render(<RunTimelinePanel />);

    expect(screen.getByTestId("run-timeline-approval-patch-preview")).toBeInTheDocument();
    expect(screen.getByText("src/app.ts")).toBeInTheDocument();
    expect(screen.getByText("-const value = 1;")).toBeInTheDocument();
    expect(screen.getByText("+const value = 2;")).toBeInTheDocument();
  });

  it("summarizes long final responses instead of replaying code blocks", () => {
    useRunTimelineStore.setState({
      initialized: true,
      order: ["run_2"],
      runs: {
        run_2: {
          id: "run_2",
          session_id: "ses_1",
          mode: "chat",
          status: "completed",
          title: "Render answer",
          created_at: "2026-06-09T00:00:00Z",
          steps: [
            {
              id: "step_final",
              run_id: "run_2",
              session_id: "ses_1",
              kind: "final",
              status: "completed",
              title: "Final response",
              summary: "Here is the answer.\n\n```ts\nconst value = 1;\n```\n\n" + "details ".repeat(80),
              started_at: "2026-06-09T00:00:00Z",
              ordinal: 1,
            },
          ],
        },
      },
    });

    render(<RunTimelinePanel />);

    expect(screen.getByText(/Here is the answer/)).toBeInTheDocument();
    expect(screen.getByText(/\[code block\]/)).toBeInTheDocument();
    expect(screen.queryByText(/const value = 1/)).toBeNull();
  });
});

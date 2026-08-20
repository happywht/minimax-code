import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RunTimelinePanel } from "../src/components/right-panel/RunTimelinePanel";
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
    expect(screen.getByText(/\[代码块\]/)).toBeInTheDocument();
    expect(screen.queryByText(/const value = 1/)).toBeNull();
  });

  it("pauses auto-follow when the user scrolls away and exposes a new-event jump", async () => {
    useRunTimelineStore.setState({
      initialized: true,
      order: ["run_scroll"],
      runs: {
        run_scroll: {
          id: "run_scroll",
          session_id: "ses_1",
          mode: "chat",
          status: "running",
          title: "Long run",
          created_at: "2026-06-09T00:00:00Z",
          steps: [
            {
              id: "step_1",
              run_id: "run_scroll",
              session_id: "ses_1",
              kind: "thought",
              status: "completed",
              title: "Plan",
              summary: "",
              started_at: "2026-06-09T00:00:00Z",
              ordinal: 1,
            },
          ],
        },
      },
    });

    render(<RunTimelinePanel />);
    const scroller = screen.getByTestId("run-timeline-scroll");
    Object.defineProperty(scroller, "scrollHeight", { configurable: true, value: 1000 });
    Object.defineProperty(scroller, "clientHeight", { configurable: true, value: 200 });
    Object.defineProperty(scroller, "scrollTop", { configurable: true, writable: true, value: 100 });
    Object.defineProperty(scroller, "scrollTo", { configurable: true, value: vi.fn() });
    fireEvent.scroll(scroller);

    act(() => {
      useRunTimelineStore.setState((state) => ({
        runs: {
          ...state.runs,
          run_scroll: {
            ...state.runs.run_scroll,
            steps: [
              ...state.runs.run_scroll.steps,
              {
                id: "step_2",
                run_id: "run_scroll",
                session_id: "ses_1",
                kind: "tool_call",
                status: "running",
                title: "read_file",
                summary: "",
                started_at: "2026-06-09T00:00:01Z",
                ordinal: 2,
              },
            ],
          },
        },
      }));
    });

    expect(await screen.findByTestId("run-timeline-new-events")).toHaveTextContent("新事件");
    fireEvent.click(screen.getByTestId("run-timeline-new-events"));
    await waitFor(() => {
      expect(screen.queryByTestId("run-timeline-new-events")).toBeNull();
    });
  });
});

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RunTimelinePanel } from "../src/components/RunTimelinePanel";
import { useRunTimelineStore } from "../src/stores";

vi.mock("../src/ipc", () => ({
  ipc: {
    on: vi.fn(() => vi.fn()),
  },
  typedIPC: {},
}));

describe("RunTimelinePanel", () => {
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
});

/**
 * Tests for the ProgressPanel — the in-column progress list.
 *
 * Historical contract: this component used to be a floating overlay
 * with a built-in collapse pill. After the three-pane refactor it
 * is purely a content piece that lives inside <RightPanel />. The
 * chrome (header, collapse, "Agent" badge positioning) moved to the
 * parent; what remains here is the task list and the small agent
 * status indicator on the right of the section header.
 *
 * B3 redesign coverage: the ledger title is the primary identifier
 * (fallback: mono task id), tasks partition into live / settled
 * sections (newest first), status badges are localized, settled
 * cards show a duration, and detail bodies expand on click.
 */
import { describe, expect, it, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ProgressPanel } from "../src/components/right-panel/ProgressPanel";
import { useTaskStore, type TaskProgressEntry } from "../src/stores";

function seedEntry(entry: Partial<TaskProgressEntry> & { task_id: string }) {
  const tasks = {
    ...useTaskStore.getState().tasks,
    [entry.task_id]: {
      status: "running",
      progress: 0,
      updated_at: Date.now(),
      ...entry,
    } as TaskProgressEntry,
  };
  useTaskStore.setState({ tasks });
}

describe("ProgressPanel", () => {
  beforeEach(() => {
    useTaskStore.setState({ tasks: {}, collapsed: false });
    localStorage.clear();
  });

  it("renders the progress section with the Agent status badge", () => {
    render(<ProgressPanel testId="pp" />);
    expect(screen.getByTestId("pp")).toBeInTheDocument();
    expect(screen.getByText("进度")).toBeInTheDocument();
    expect(screen.getByTestId("progress-agent-status")).toBeInTheDocument();
  });

  it("shows the empty state when there are no tasks", () => {
    render(<ProgressPanel testId="pp" />);
    expect(screen.getByTestId("pp-empty")).toBeInTheDocument();
    expect(screen.getByText("暂无运行中任务")).toBeInTheDocument();
  });

  it("renders a task row and dismisses it on click", () => {
    useTaskStore.getState().upsert({
      task_id: "t-1",
      progress: 0.42,
      status: "running",
      message: "thinking…",
    });
    render(<ProgressPanel testId="pp" />);
    const row = screen.getByTestId("task-row-t-1");
    expect(row).toBeInTheDocument();
    expect(screen.getByTestId("task-status-running")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("移除任务"));
    expect(useTaskStore.getState().tasks["t-1"]).toBeUndefined();
  });

  it("shows a running-count chip when at least one task is running", () => {
    useTaskStore.getState().upsert({
      task_id: "t-2",
      progress: 0.1,
      status: "running",
    });
    useTaskStore.getState().upsert({
      task_id: "t-3",
      progress: 1,
      status: "done",
    });
    render(<ProgressPanel testId="pp" />);
    expect(screen.getByTestId("pp-running-count").textContent).toContain("1");
  });

  it("renders a done task's persisted result text (v1.2.0)", () => {
    // Mirror what entryFromRow produces after a ledger refresh: a done
    // entry carrying the run's result (e.g. a scheduled prompt's reply).
    seedEntry({
      task_id: "t-r",
      status: "done",
      progress: 1,
      result: "LLM digest: 3 commits since yesterday",
    });
    render(<ProgressPanel testId="pp" />);
    const result = screen.getByTestId("task-detail-t-r");
    expect(result).toBeInTheDocument();
    expect(result.textContent).toContain("LLM digest: 3 commits");
  });

  it("keeps a persisted result across later progress ticks", () => {
    useTaskStore.setState({
      tasks: {
        "t-k": {
          task_id: "t-k",
          status: "done",
          progress: 1,
          result: "kept",
          updated_at: Date.now(),
        },
      },
    });
    // A late ``task.progress`` event carries no ``result`` — it must not
    // clobber the one hydrated from the ledger.
    useTaskStore.getState().upsert({
      task_id: "t-k",
      progress: 1,
      status: "done",
    });
    expect(useTaskStore.getState().tasks["t-k"].result).toBe("kept");
  });

  // ------------------------------------------------------------------
  // B3 redesign
  // ------------------------------------------------------------------

  it("shows the ledger title as the primary identifier (B3)", () => {
    seedEntry({
      task_id: "t-title",
      title: "重建代码库索引",
      status: "running",
      progress: 0.5,
    });
    render(<ProgressPanel testId="pp" />);
    expect(screen.getByTestId("task-title-t-title").textContent).toBe("重建代码库索引");
    // The mono task id is demoted to the metadata line but still present.
    expect(screen.getByText("t-title")).toBeInTheDocument();
  });

  it("falls back to the task id when the ledger row has no title (B3)", () => {
    useTaskStore.getState().upsert({
      task_id: "t-plain",
      progress: 0.2,
      status: "running",
    });
    render(<ProgressPanel testId="pp" />);
    expect(screen.getByTestId("task-title-t-plain").textContent).toBe("t-plain");
  });

  it("partitions live tasks before settled ones (B3)", () => {
    const now = Date.now();
    seedEntry({
      task_id: "t-old-done",
      status: "done",
      progress: 1,
      updated_at: now - 1000,
    });
    seedEntry({
      task_id: "t-fresh-run",
      status: "running",
      progress: 0.3,
      updated_at: now,
    });
    render(<ProgressPanel testId="pp" />);
    const liveList = screen.getByTestId("pp-task-list-live");
    const settledList = screen.getByTestId("pp-task-list-settled");
    expect(liveList).toContainElement(screen.getByTestId("task-row-t-fresh-run"));
    expect(settledList).toContainElement(screen.getByTestId("task-row-t-old-done"));
    // Section headers are rendered in live-then-settled order.
    const headers = screen
      .getAllByText(/运行中|最近完成/)
      .map((el) => el.textContent);
    expect(headers.indexOf("运行中")).toBeLessThan(headers.lastIndexOf("最近完成"));
  });

  it("sorts each section newest first (B3)", () => {
    const now = Date.now();
    seedEntry({
      task_id: "t-older",
      status: "done",
      progress: 1,
      updated_at: now - 5000,
    });
    seedEntry({
      task_id: "t-newer",
      status: "done",
      progress: 1,
      updated_at: now,
    });
    render(<ProgressPanel testId="pp" />);
    const settled = screen.getByTestId("pp-task-list-settled");
    const rows = settled.querySelectorAll("li");
    expect(rows[0]).toHaveAttribute("data-testid", "task-row-t-newer");
    expect(rows[1]).toHaveAttribute("data-testid", "task-row-t-older");
  });

  it("shows a duration line on settled tasks with a known creation time (B3)", () => {
    const now = Date.now();
    seedEntry({
      task_id: "t-dur",
      status: "done",
      progress: 1,
      created_at: now - 65_000,
      updated_at: now,
    });
    render(<ProgressPanel testId="pp" />);
    const duration = screen.getByTestId("task-duration-t-dur");
    expect(duration.textContent).toContain("耗时");
    expect(duration.textContent).toContain("01:05");
  });

  it("localizes status badges for all five states (B3)", () => {
    seedEntry({ task_id: "t-pend", status: "pending", progress: 0 });
    seedEntry({ task_id: "t-done", status: "done", progress: 1 });
    seedEntry({ task_id: "t-err", status: "error", progress: 1 });
    seedEntry({ task_id: "t-cancel", status: "cancelled", progress: 1 });
    render(<ProgressPanel testId="pp" />);
    expect(screen.getByTestId("task-status-pending").textContent).toBe("等待中");
    expect(screen.getByTestId("task-status-done").textContent).toBe("已完成");
    expect(screen.getByTestId("task-status-error").textContent).toBe("失败");
    expect(screen.getByTestId("task-status-cancelled").textContent).toBe("已取消");
  });

  it("shows the progress percentage next to the bar (B3)", () => {
    seedEntry({ task_id: "t-pct", status: "running", progress: 0.42 });
    render(<ProgressPanel testId="pp" />);
    expect(screen.getByTestId("task-percent-t-pct").textContent).toBe("42%");
  });

  it("expands and collapses the detail body on click (B3)", () => {
    seedEntry({
      task_id: "t-exp",
      status: "done",
      progress: 1,
      result: "line one\nline two\nline three\nline four",
    });
    render(<ProgressPanel testId="pp" />);
    const toggle = screen.getByTestId("task-detail-toggle-t-exp");
    const body = screen.getByTestId("task-detail-t-exp");
    // Collapsed by default (2-line clamp).
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(body.className).toContain("line-clamp-2");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(body.className).not.toContain("line-clamp-2");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(body.className).toContain("line-clamp-2");
  });
});

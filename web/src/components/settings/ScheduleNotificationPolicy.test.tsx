import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { ScheduleContext } from "../../types/ipc";

vi.mock("../../stores", () => ({
  useSessionStore: (selector: (state: unknown) => unknown) => selector({ projects: [] }),
  useModelStore: (selector: (state: unknown) => unknown) => selector({ models: [] }),
  useScheduleStore: {},
}));
vi.mock("../../ipc", () => ({ ipc: {}, typedIPC: {} }));
import { ScheduleContextFields, ScheduleSnapshot } from "./ScheduleContextFields";

it("selects and displays the attention-only notification policy", () => {
  const context: ScheduleContext = { project_id: "p", provider_id: "v", model: "m",
    budget_tokens: 100, mode: "execute", timezone: "Asia/Shanghai" };
  const change = vi.fn();
  render(<ScheduleContextFields value={context} onChange={change} />);
  expect(screen.getByLabelText("定时任务通知策略")).toHaveValue("all");
  fireEvent.change(screen.getByLabelText("定时任务通知策略"), { target: { value: "attention" } });
  expect(change).toHaveBeenCalledWith({ ...context, notification_policy: "attention" });
  render(<ScheduleSnapshot job={{ id: "j", name: "J", cron: "* * * * *", prompt: "p", enabled: true,
    last_run_at: null, next_run_at: null, context: { ...context, notification_policy: "attention" } }} />);
  expect(screen.getByText("运行通知：仅失败或需要处理时通知")).toBeInTheDocument();
});

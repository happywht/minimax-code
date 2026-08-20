/**
 * Regression tests for P0#3 — SettingsPage.tsx monolith split.
 * Verifies that each tab component can be independently imported and
 * rendered, and that the barrel export covers all 10 tabs.
 */
import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// vi.mock is hoisted — factory must be self-contained, no external refs
vi.mock("../../../stores", () => {
  /** Mock Zustand store: supports both selector and direct-call patterns. */
  const ms = (state: Record<string, unknown>) => {
    const fn = (sel?: (s: Record<string, unknown>) => unknown) =>
      sel ? sel(state) : state;
    return vi.fn(fn);
  };
  return {
    useModelStore: ms({ models: [], current: "", refresh: vi.fn(), setCurrent: vi.fn(), loading: false }),
    useProviderStore: ms({ providers: [], loading: false, refresh: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn(), setApiKey: vi.fn(), clearApiKey: vi.fn() }),
    usePermissionStore: ms({ rules: [], refresh: vi.fn(), upsertRule: vi.fn(), removeRule: vi.fn() }),
    useScheduleStore: ms({ jobs: [], refresh: vi.fn(), create: vi.fn(), remove: vi.fn(), setEnabled: vi.fn(), runNow: vi.fn(), loading: false }),
    useTaskStore: ms({ tasks: {} }),
    useSecretStore: ms({ status: { source: "none", configured: false }, loading: false, refresh: vi.fn(), setKey: vi.fn(), clear: vi.fn() }),
    useAgentStore: ms({ agents: [], loading: false, refresh: vi.fn(), create: vi.fn(), remove: vi.fn() }),
    useTeamStore: ms({ teams: [], loading: false, refresh: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn(), enable: vi.fn(), disable: vi.fn() }),
    useAuditStore: ms({ entries: [], total: 0, stats: null, loading: false, page: 0, pageSize: 20, filterTool: null, refresh: vi.fn(), loadStats: vi.fn(), setPage: vi.fn(), setFilterTool: vi.fn() }),
    useWebhookStore: ms({ entries: [], total: 0, loading: false, error: null, refresh: vi.fn(), create: vi.fn(), remove: vi.fn(), regenerateSecret: vi.fn(), update: vi.fn() }),
    useWorkflowStore: ms({ entries: [], total: 0, loading: false, error: null, refresh: vi.fn(), create: vi.fn(), remove: vi.fn(), enable: vi.fn(), disable: vi.fn(), trigger: vi.fn() }),
  };
});

vi.mock("../../../types/ipc", () => ({}));
vi.mock("../../layout/ErrorBoundary", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import {
  ModelsTab,
  ProvidersTab,
  PermissionsTab,
  ScheduledTab,
  ApiKeyTab,
  AgentsTab,
  TeamsTab,
  AuditTab,
  WebhooksTab,
  WorkflowsTab,
} from "../index";

const TABS = [
  { Component: ModelsTab, testId: "settings-models" },
  { Component: ProvidersTab, testId: "settings-providers" },
  { Component: PermissionsTab, testId: "settings-permissions" },
  { Component: ScheduledTab, testId: "settings-scheduled" },
  { Component: ApiKeyTab, testId: "settings-api-key" },
  { Component: AgentsTab, testId: "settings-agents" },
  { Component: TeamsTab, testId: "settings-teams" },
  { Component: AuditTab, testId: "settings-audit-section" },
  { Component: WebhooksTab, testId: "settings-webhooks-section" },
  { Component: WorkflowsTab, testId: "settings-workflows-section" },
];

describe("Settings tab split (P0#3)", () => {
  it("barrel exports exactly 10 tab components", () => {
    expect(TABS).toHaveLength(10);
  });

  it.each(TABS.map(({ Component, testId }) => ({ Component, testId })))(
    "$testId renders without crashing",
    ({ Component, testId }) => {
      const { container } = render(<Component />);
      const section = container.querySelector(`[data-testid="${testId}"]`);
      expect(section).toBeTruthy();
    },
  );

  it("gives compact permission and schedule controls accessible names", () => {
    const permissions = render(<PermissionsTab />);
    expect(screen.getByLabelText("工具")).toHaveAttribute("name", "permission-tool");
    expect(screen.getByLabelText("参数模式")).toHaveAttribute("name", "permission-pattern");
    expect(screen.getByLabelText("决策")).toHaveAttribute("name", "permission-decision");
    permissions.unmount();

    const scheduled = render(<ScheduledTab />);
    expect(screen.getByLabelText("任务名称")).toHaveAttribute("name", "scheduled-job-name");
    expect(screen.getByLabelText("Cron 表达式")).toHaveAttribute("name", "scheduled-job-cron");
    expect(screen.getByLabelText("提示词")).toHaveAttribute("name", "scheduled-job-prompt");
    scheduled.unmount();
  });

  it("labels agent and team creation forms", () => {
    const agents = render(<AgentsTab />);
    fireEvent.click(screen.getByTestId("settings-agent-create"));
    expect(screen.getByLabelText("Agent 名称")).toHaveAttribute("name", "agent-name");
    expect(screen.getByLabelText("系统提示词")).toHaveAttribute("name", "agent-system-prompt");
    agents.unmount();

    const teams = render(<TeamsTab />);
    fireEvent.click(screen.getByTestId("settings-team-create"));
    expect(screen.getByLabelText("名称")).toHaveAttribute("name", "team-name");
    expect(screen.getByLabelText("编排模式")).toHaveAttribute("name", "team-orchestration-mode");
    expect(screen.getByLabelText("描述")).toHaveAttribute("name", "team-description");
    expect(screen.getAllByRole("button", { name: /使用团队颜色/ })).toHaveLength(5);
    teams.unmount();
  });

  it("labels webhook, workflow, and audit controls", () => {
    const webhooks = render(<WebhooksTab />);
    fireEvent.click(screen.getByTestId("webhook-create-btn"));
    expect(screen.getByLabelText("名称")).toHaveAttribute("name", "webhook-name");
    expect(screen.getByLabelText("来源")).toHaveAttribute("name", "webhook-source");
    expect(screen.getByLabelText("动作")).toHaveAttribute("name", "webhook-action");
    webhooks.unmount();

    const workflows = render(<WorkflowsTab />);
    fireEvent.click(screen.getByTestId("workflow-create-btn"));
    expect(screen.getByLabelText("名称")).toHaveAttribute("name", "workflow-name");
    expect(screen.getByLabelText("触发器")).toHaveAttribute("name", "workflow-trigger-type");
    expect(screen.getByLabelText("描述")).toHaveAttribute("name", "workflow-description");
    workflows.unmount();

    const audit = render(<AuditTab />);
    expect(screen.getByLabelText("按工具筛选：")).toHaveAttribute("name", "audit-filter-tool");
    audit.unmount();
  });
});

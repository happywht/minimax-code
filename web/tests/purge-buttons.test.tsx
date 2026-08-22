/**
 * Tests for the audit / notification purge buttons (P1-6) — both flows
 * are confirm-gated and route through the store purge methods already
 * wired to `audit.purge` / `notification.purge`.
 *
 * The stores import ``typedIPC`` directly from ``ipc/client`` (not the
 * barrel), so the module mock targets the client module itself.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

const { purgeAudit, confirmMock, toastSuccess, notificationPurge, listNotifications } =
  vi.hoisted(() => ({
    purgeAudit: vi.fn(),
    confirmMock: vi.fn(),
    toastSuccess: vi.fn(),
    notificationPurge: vi.fn(),
    listNotifications: vi.fn(),
  }));

vi.mock("../src/ipc/client", () => ({
  typedIPC: {
    listAudit: vi.fn(async () => ({ entries: [], total: 0 })),
    auditStats: vi.fn(async () => ({ total: 3, by_tool: { read_file: 3 }, by_status: { success: 3 } })),
    purgeAudit,
    listNotifications,
    purgeNotifications: notificationPurge,
  },
  // notificationStore imports ``ipc`` for its WS listeners (_init only —
  // never called in these tests, but the export must exist).
  ipc: { on: vi.fn(() => vi.fn()) },
}));
vi.mock("../src/components/modals/ConfirmationDialog", () => ({
  requestConfirmation: confirmMock,
  confirmationBus: { reset: vi.fn() },
}));
vi.mock("../src/components/layout/ErrorBoundary", () => ({
  toast: { success: toastSuccess, error: vi.fn(), info: vi.fn() },
}));

import { AuditTab } from "../src/components/settings/AuditTab";
import { NotificationCenter } from "../src/components/layout/NotificationCenter";
import { useNotificationStore } from "../src/stores/notificationStore";

describe("AuditTab purge (P1-6)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    confirmMock.mockResolvedValue(true);
    purgeAudit.mockResolvedValue({ deleted: 3 });
  });

  it("purges every audit row after confirmation and toasts the count", async () => {
    render(<AuditTab />);
    // The button stays disabled until stats land (total 0 + no stats).
    await waitFor(() => {
      expect(screen.getByTestId("settings-audit-purge")).toBeEnabled();
    });

    fireEvent.click(screen.getByTestId("settings-audit-purge"));
    await waitFor(() => expect(purgeAudit).toHaveBeenCalledTimes(1));

    // "before now" — every persisted row is older than the purge instant.
    const beforeIso = purgeAudit.mock.calls[0][0] as string;
    expect(new Date(beforeIso).getTime()).toBeLessThanOrEqual(Date.now());
    expect(toastSuccess).toHaveBeenCalledWith("已清理 3 条审计记录");
  });

  it("skips the purge when the confirmation is declined", async () => {
    confirmMock.mockResolvedValue(false);
    render(<AuditTab />);
    await waitFor(() => {
      expect(screen.getByTestId("settings-audit-purge")).toBeEnabled();
    });

    fireEvent.click(screen.getByTestId("settings-audit-purge"));
    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    expect(purgeAudit).not.toHaveBeenCalled();
  });
});

const READ_ENTRY = {
  id: "n1",
  type: "info",
  title: "已读通知",
  body: "旧消息",
  read: true,
  created_at: new Date().toISOString(),
};
const UNREAD_ENTRY = {
  id: "n2",
  type: "info",
  title: "未读通知",
  body: "新消息",
  read: false,
  created_at: new Date().toISOString(),
};

describe("NotificationCenter purge-read (P1-6)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    confirmMock.mockResolvedValue(true);
    notificationPurge.mockResolvedValue({ purged: 2 });
    // Drive the list through the refresh path the panel actually uses
    // (its mount effect calls refresh({limit: 50})).
    listNotifications.mockResolvedValue({ entries: [READ_ENTRY, UNREAD_ENTRY], total: 2 });
    useNotificationStore.setState({ entries: [], total: 0, unreadCount: 0, loading: false });
  });

  it("shows the button only when read entries exist", async () => {
    render(<NotificationCenter />);
    await waitFor(() => {
      expect(screen.getByTestId("notification-purge-read")).toBeInTheDocument();
    });

    // All-unread refresh — the button must disappear with the data.
    listNotifications.mockResolvedValue({ entries: [UNREAD_ENTRY], total: 1 });
    await act(async () => {
      await useNotificationStore.getState().refresh({ limit: 50 });
    });
    await waitFor(() => {
      expect(screen.queryByTestId("notification-purge-read")).not.toBeInTheDocument();
    });
  });

  it("purges read-only entries after confirmation", async () => {
    render(<NotificationCenter />);
    await waitFor(() => {
      expect(screen.getByTestId("notification-purge-read")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("notification-purge-read"));
    await waitFor(() => expect(notificationPurge).toHaveBeenCalledTimes(1));

    const [beforeIso, readOnly] = notificationPurge.mock.calls[0];
    expect(new Date(beforeIso as string).getTime()).toBeLessThanOrEqual(Date.now());
    // read-only: unread notifications must survive the purge.
    expect(readOnly).toBe(true);
  });
});

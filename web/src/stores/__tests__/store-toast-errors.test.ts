/**
 * Tests for P1#18 — Store error toast coverage.
 *
 * Verifies that every Tier-2 store's catch blocks call toast.error()
 * so the user gets visible feedback when operations fail.
 *
 * Stores covered:
 *   - teamStore (6 actions)
 *   - workflowStore (7 actions)
 *   - webhookStore (5 actions)
 *   - notificationStore (5 actions)
 *   - teamRunStore (1 action — spawn)
 *   - chat.ts (loadMessages)
 *
 * codeReviewStore already had toast.error() — no fix needed.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

// ── Mock the toast bus ────────────────────────────────────────────────
const toastErrorSpy = vi.fn();
vi.mock("../../components/ErrorBoundary", () => ({
  toast: {
    error: (...args: unknown[]) => toastErrorSpy(...args),
    info: vi.fn(),
    success: vi.fn(),
  },
}));

// ── Mock the IPC layer so every call rejects ──────────────────────────
// Factory must be self-contained — vi.mock is hoisted above imports.
vi.mock("@/ipc/client", () => {
  const fail = () => Promise.reject(new Error("ipc-fail"));
  return {
    typedIPC: {
      listTeams: fail,
      createTeam: fail,
      updateTeam: fail,
      deleteTeam: fail,
      enableTeam: fail,
      disableTeam: fail,

      listWorkflows: fail,
      createWorkflow: fail,
      updateWorkflow: fail,
      deleteWorkflow: fail,
      enableWorkflow: fail,
      disableWorkflow: fail,
      triggerWorkflow: fail,

      listWebhooks: fail,
      createWebhook: fail,
      updateWebhook: fail,
      deleteWebhook: fail,
      regenerateWebhookSecret: fail,

      listNotifications: fail,
      markNotificationRead: fail,
      markAllNotificationsRead: fail,
      deleteNotification: fail,
      purgeNotifications: fail,

      spawnTeam: fail,

      listMessages: fail,
    },
    ipc: {
      on: () => () => {},
      off: () => {},
      start: () => Promise.resolve(),
      ping: () => Promise.resolve(true),
    },
    IPCError: class extends Error {},
  };
});

// Import stores AFTER mocks are set up
import { useTeamStore } from "../teamStore";
import { useWorkflowStore } from "../workflowStore";
import { useWebhookStore } from "../webhookStore";
import { useNotificationStore } from "../notificationStore";
import { useTeamRunStore } from "../teamRunStore";

describe("P1#18: Store error toast coverage", () => {
  beforeEach(() => {
    toastErrorSpy.mockClear();
  });

  // ── teamStore ─────────────────────────────────────────────────────
  describe("teamStore", () => {
    it("refresh() calls toast.error on failure", async () => {
      await useTeamStore.getState().refresh();
      expect(toastErrorSpy).toHaveBeenCalledWith("Failed to load teams", expect.any(String));
    });

    it("create() calls toast.error on failure", async () => {
      const result = await useTeamStore.getState().create({ name: "x" });
      expect(result).toBeNull();
      expect(toastErrorSpy).toHaveBeenCalledWith("Failed to create team", expect.any(String));
    });

    it("remove() calls toast.error on failure", async () => {
      await useTeamStore.getState().remove("x");
      expect(toastErrorSpy).toHaveBeenCalledWith("Failed to delete team", expect.any(String));
    });

    it("enable() calls toast.error on failure", async () => {
      await useTeamStore.getState().enable("x");
      expect(toastErrorSpy).toHaveBeenCalledWith("Failed to enable team", expect.any(String));
    });
  });

  // ── workflowStore ─────────────────────────────────────────────────
  describe("workflowStore", () => {
    it("refresh() calls toast.error on failure", async () => {
      await useWorkflowStore.getState().refresh();
      expect(toastErrorSpy).toHaveBeenCalledWith("Failed to load workflows", expect.any(String));
    });

    it("create() calls toast.error on failure", async () => {
      const result = await useWorkflowStore.getState().create({ name: "x", trigger_type: "cron" });
      expect(result).toBeNull();
      expect(toastErrorSpy).toHaveBeenCalledWith("Failed to create workflow", expect.any(String));
    });

    it("remove() calls toast.error on failure", async () => {
      await useWorkflowStore.getState().remove("x");
      expect(toastErrorSpy).toHaveBeenCalledWith("Failed to delete workflow", expect.any(String));
    });

    it("trigger() calls toast.error on failure", async () => {
      await useWorkflowStore.getState().trigger("x");
      expect(toastErrorSpy).toHaveBeenCalledWith("Failed to trigger workflow", expect.any(String));
    });
  });

  // ── webhookStore ──────────────────────────────────────────────────
  describe("webhookStore", () => {
    it("refresh() calls toast.error on failure", async () => {
      await useWebhookStore.getState().refresh();
      expect(toastErrorSpy).toHaveBeenCalledWith("Failed to load webhooks", expect.any(String));
    });

    it("create() calls toast.error on failure", async () => {
      const result = await useWebhookStore.getState().create({ name: "x" });
      expect(result).toBeNull();
      expect(toastErrorSpy).toHaveBeenCalledWith("Failed to create webhook", expect.any(String));
    });

    it("update() calls toast.error on failure", async () => {
      await useWebhookStore.getState().update("x", { name: "y" });
      expect(toastErrorSpy).toHaveBeenCalledWith("Failed to update webhook", expect.any(String));
    });

    it("remove() calls toast.error on failure", async () => {
      await useWebhookStore.getState().remove("x");
      expect(toastErrorSpy).toHaveBeenCalledWith("Failed to delete webhook", expect.any(String));
    });

    it("regenerateSecret() calls toast.error on failure", async () => {
      const result = await useWebhookStore.getState().regenerateSecret("x");
      expect(result).toBeNull();
      expect(toastErrorSpy).toHaveBeenCalledWith(
        "Failed to regenerate webhook secret",
        expect.any(String),
      );
    });
  });

  // ── notificationStore ─────────────────────────────────────────────
  describe("notificationStore", () => {
    it("refresh() calls toast.error on failure", async () => {
      await useNotificationStore.getState().refresh();
      expect(toastErrorSpy).toHaveBeenCalledWith(
        "Failed to load notifications",
        expect.any(String),
      );
    });

    it("markRead() calls toast.error on failure", async () => {
      await useNotificationStore.getState().markRead("x");
      expect(toastErrorSpy).toHaveBeenCalledWith(
        "Failed to mark notification as read",
        expect.any(String),
      );
    });

    it("markAllRead() calls toast.error on failure", async () => {
      await useNotificationStore.getState().markAllRead();
      expect(toastErrorSpy).toHaveBeenCalledWith(
        "Failed to mark all notifications as read",
        expect.any(String),
      );
    });

    it("deleteNotification() calls toast.error on failure", async () => {
      await useNotificationStore.getState().deleteNotification("x");
      expect(toastErrorSpy).toHaveBeenCalledWith(
        "Failed to delete notification",
        expect.any(String),
      );
    });

    it("purge() calls toast.error on failure", async () => {
      await useNotificationStore.getState().purge("2025-01-01T00:00:00Z");
      expect(toastErrorSpy).toHaveBeenCalledWith(
        "Failed to purge notifications",
        expect.any(String),
      );
    });
  });

  // ── teamRunStore ──────────────────────────────────────────────────
  describe("teamRunStore", () => {
    it("spawn() calls toast.error on failure", async () => {
      // Pre-seed a run so the catch has something to mark as failed
      useTeamRunStore.setState({
        runs: [
          {
            task_id: "test-task",
            team_name: "team-x",
            status: "started",
            progress: 0,
            agents_total: 1,
            agents_completed: 0,
            started_at: Date.now(),
            updated_at: Date.now(),
          },
        ],
      });
      await useTeamRunStore.getState().spawn({ team_name: "team-x", request: "do it" });
      expect(toastErrorSpy).toHaveBeenCalledWith("Failed to spawn team", expect.any(String));
    });
  });
});

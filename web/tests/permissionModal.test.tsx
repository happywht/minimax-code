/**
 * Tests for the PermissionRequestModal — verifies the modal renders on
 * a `permission.request` event, displays the tool + args, and that
 * the 允许/拒绝 buttons call `resolve()` (which POSTs to the sidecar
 * via the typed IPC).
 *
 * The mock IPC backend in `web/src/ipc/client.ts` echoes
 * `permission.resolve` with `{ ok: true }` so we can drive the flow
 * end-to-end without a Tauri shell.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { PermissionRequestModal } from "../src/components/PermissionRequestModal";
import {
  usePermissionStore,
  _resetPermissionStoreListeners,
  type PendingPermission,
} from "../src/stores";
import { ipc, typedIPC } from "../src/ipc";
import { StreamEvent } from "../src/types/ipc";

vi.mock("../src/components/ErrorBoundary", () => ({
  toast: { error: vi.fn(), info: vi.fn(), success: vi.fn() },
  toastBus: { push: vi.fn(), dismiss: vi.fn() },
}));

describe("PermissionRequestModal", () => {
  beforeEach(() => {
    usePermissionStore.setState({
      alwaysAllow: false,
      rules: [],
      loading: false,
      pending: {},
      resolving: {},
    });
    // NOTE: do NOT reset listeners — they're wired once at module
    // import. Resetting them would break the "responds to incoming
    // permission.request events" test, which depends on the
    // subscription surviving across renders.
  });

  it("renders nothing when no pending request", () => {
    render(<PermissionRequestModal testId="m" />);
    expect(screen.queryByTestId("m")).toBeNull();
  });

  it("shows the modal when a request is enqueued", () => {
    const pending: PendingPermission = {
      request_id: "perm_abc",
      tool: "exec_command",
      args: { cmd: ["rm", "-rf", "/"] },
      received_at: Date.now(),
    };
    usePermissionStore.setState({ pending: { perm_abc: pending } });
    render(<PermissionRequestModal testId="m" />);
    expect(screen.getByTestId("m")).toBeInTheDocument();
    expect(screen.getByTestId("permission-request-modal-tool")).toHaveTextContent("exec_command");
    expect(screen.getByTestId("permission-request-modal-args")).toHaveTextContent("rm");
  });

  it("shows patch preview for file-write approvals", () => {
    usePermissionStore.setState({
      pending: {
        perm_write: {
          request_id: "perm_write",
          tool: "write_file",
          args: { path: "README.md", content: "# Title\nHello\n" },
          received_at: Date.now(),
        },
      },
    });
    render(<PermissionRequestModal />);

    expect(screen.getByTestId("permission-request-modal-patch-preview")).toBeInTheDocument();
    expect(screen.getByText("README.md")).toBeInTheDocument();
    expect(screen.getByText("+# Title")).toBeInTheDocument();
    expect(screen.getByText("+Hello")).toBeInTheDocument();
  });

  it("clicking 允许 calls resolve(allow) and dismisses the modal", async () => {
    const resolveSpy = vi.spyOn(typedIPC, "resolvePermission").mockResolvedValue({
      ok: true,
      request_id: "perm_xyz",
      decision: "allow",
    });

    usePermissionStore.setState({
      pending: {
        perm_xyz: {
          request_id: "perm_xyz",
          tool: "exec_command",
          args: { cmd: ["echo", "hi"] },
          received_at: Date.now(),
        },
      },
    });
    render(<PermissionRequestModal />);
    fireEvent.click(screen.getByTestId("permission-request-modal-allow"));
    await waitFor(() => {
      expect(resolveSpy).toHaveBeenCalledWith({
        request_id: "perm_xyz",
        decision: "allow",
      });
    });
    // The store optimistically drops the request.
    await waitFor(() => {
      expect(usePermissionStore.getState().pending.perm_xyz).toBeUndefined();
    });
    resolveSpy.mockRestore();
  });

  it("clicking 拒绝 calls resolve(deny) and dismisses the modal", async () => {
    const resolveSpy = vi.spyOn(typedIPC, "resolvePermission").mockResolvedValue({
      ok: true,
      request_id: "perm_d1",
      decision: "deny",
    });

    usePermissionStore.setState({
      pending: {
        perm_d1: {
          request_id: "perm_d1",
          tool: "write_file",
          args: { path: "/etc/passwd", content: "x" },
          received_at: Date.now(),
        },
      },
    });
    render(<PermissionRequestModal />);
    fireEvent.click(screen.getByTestId("permission-request-modal-deny"));
    await waitFor(() => {
      expect(resolveSpy).toHaveBeenCalledWith({
        request_id: "perm_d1",
        decision: "deny",
      });
    });
    resolveSpy.mockRestore();
  });

  it("restores the request when resolving the permission fails", async () => {
    const resolveSpy = vi.spyOn(typedIPC, "resolvePermission").mockRejectedValue(
      new Error("connection lost"),
    );
    usePermissionStore.setState({
      pending: {
        perm_retry: {
          request_id: "perm_retry",
          tool: "exec_command",
          args: { cmd: ["pnpm", "test"] },
          received_at: Date.now(),
        },
      },
    });

    render(<PermissionRequestModal />);
    fireEvent.click(screen.getByTestId("permission-request-modal-allow"));

    await waitFor(() => {
      expect(resolveSpy).toHaveBeenCalledWith({
        request_id: "perm_retry",
        decision: "allow",
      });
      expect(usePermissionStore.getState().resolving.perm_retry).toBeUndefined();
      expect(usePermissionStore.getState().pending.perm_retry).toBeDefined();
      expect(screen.getByTestId("permission-request-modal")).toBeInTheDocument();
    });
    resolveSpy.mockRestore();
  });

  it("approves with Enter and denies with Escape", async () => {
    const resolveSpy = vi.spyOn(typedIPC, "resolvePermission").mockResolvedValue({
      ok: true,
      request_id: "perm_keys",
      decision: "allow",
    });

    usePermissionStore.setState({
      pending: {
        perm_keys: {
          request_id: "perm_keys",
          tool: "edit_file",
          args: { path: "app.ts" },
          received_at: Date.now(),
        },
      },
    });
    render(<PermissionRequestModal />);

    fireEvent.keyDown(window, { key: "Enter" });
    await waitFor(() => {
      expect(resolveSpy).toHaveBeenCalledWith({
        request_id: "perm_keys",
        decision: "allow",
      });
    });

    resolveSpy.mockClear();
    act(() => {
      usePermissionStore.setState({
        pending: {
          perm_keys_2: {
            request_id: "perm_keys_2",
            tool: "edit_file",
            args: { path: "app.ts" },
            received_at: Date.now(),
          },
        },
      });
    });
    await waitFor(() => {
      expect(screen.getByTestId("permission-request-modal")).toHaveAttribute(
        "data-request-id",
        "perm_keys_2",
      );
    });

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => {
      expect(resolveSpy).toHaveBeenCalledWith({
        request_id: "perm_keys_2",
        decision: "deny",
      });
    });
    resolveSpy.mockRestore();
  });

  it("responds to incoming permission.request events via the IPC client", async () => {
    render(<PermissionRequestModal />);
    // The store wires its listener at module-import time, so by the
    // time we render() it's already subscribed. Emit a synthetic
    // event through the mock IPC client and verify the modal
    // opens.
    await act(async () => {
      ipc._emit(StreamEvent.PermissionRequest, {
        request_id: "perm_live",
        tool: "echo",
        args: { text: "live" },
      });
    });
    await waitFor(() => {
      expect(screen.getByTestId("permission-request-modal")).toBeInTheDocument();
    });
    expect(screen.getByTestId("permission-request-modal-tool")).toHaveTextContent("echo");
  });

  it("alwaysAllow=true auto-resolves the request without showing the modal", async () => {
    const resolveSpy = vi.spyOn(typedIPC, "resolvePermission").mockResolvedValue({
      ok: true,
      request_id: "perm_aa",
      decision: "allow",
    });
    usePermissionStore.setState({ alwaysAllow: true });
    render(<PermissionRequestModal />);
    await act(async () => {
      ipc._emit(StreamEvent.PermissionRequest, {
        request_id: "perm_aa",
        tool: "exec_command",
        args: {},
      });
    });
    await waitFor(() => {
      expect(resolveSpy).toHaveBeenCalledWith({
        request_id: "perm_aa",
        decision: "allow",
      });
    });
    // The modal never opens because the request was auto-resolved.
    expect(screen.queryByTestId("permission-request-modal")).toBeNull();
    resolveSpy.mockRestore();
  });
});

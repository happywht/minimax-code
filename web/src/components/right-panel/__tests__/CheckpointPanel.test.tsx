import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CheckpointPanel } from "../CheckpointPanel";
import { typedIPC } from "../../../ipc";
import { useSessionStore } from "../../../stores";
import type { Checkpoint } from "../../../types/ipc";
import { requestConfirmation } from "../../modals/ConfirmationDialog";
import { toast } from "../../layout/ErrorBoundary";

vi.mock("../../modals/ConfirmationDialog", () => ({ requestConfirmation: vi.fn().mockResolvedValue(true) }));
vi.mock("../../layout/ErrorBoundary", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const checkpoint = (source_root: string | null): Checkpoint => ({
  id: "ckpt1", session_id: "s", label: "saved", message: "", git_stash_ref: null,
  branch: null, tracked_files: [], untracked_files: [], has_untracked_snapshot: false,
  source_root, created_at: "2026-01-01T00:00:00Z",
});

beforeEach(() => {
  vi.clearAllMocks();
  useSessionStore.setState({ currentSessionId: "s" });
  vi.mocked(requestConfirmation).mockResolvedValue(true);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("requires a verified original directory before legacy restore or diff", async () => {
  vi.spyOn(typedIPC, "listCheckpoints").mockResolvedValue({ checkpoints: [checkpoint(null)], count: 1 });
  const restore = vi.spyOn(typedIPC, "restoreCheckpoint").mockResolvedValue({ result: {
    checkpoint_id: "ckpt1", restored: true, applied_stash: false, restored_untracked: [],
    skipped_existing: [], warnings: [],
  } });
  const diff = vi.spyOn(typedIPC, "diffCheckpoint").mockResolvedValue({ checkpoint_id: "ckpt1", available: false, patch: "", files: [] });
  render(<CheckpointPanel />);
  const restoreButton = await screen.findByTestId("checkpoint-panel-restore-ckpt1");
  const diffButton = screen.getByTestId("checkpoint-panel-diff-ckpt1");
  expect(restoreButton).toBeDisabled();
  expect(diffButton).toBeDisabled();
  fireEvent.change(screen.getByLabelText("原工作目录"), { target: { value: "/workspace/old" } });
  expect(restoreButton).toBeDisabled();
  fireEvent.click(screen.getByLabelText("我已核实此目录是创建检查点时使用的目录"));
  fireEvent.click(diffButton);
  await waitFor(() => expect(diff).toHaveBeenCalledWith("ckpt1", { cwd: "/workspace/old", legacy_source_confirmed: true }));
  fireEvent.click(restoreButton);
  await waitFor(() => expect(restore).toHaveBeenCalledWith("ckpt1", { cwd: "/workspace/old", legacy_source_confirmed: true }));
  fireEvent.change(screen.getByLabelText("原工作目录"), { target: { value: "/workspace/other" } });
  expect(restoreButton).toBeDisabled();
});

it("restores a bound checkpoint without an override", async () => {
  vi.spyOn(typedIPC, "listCheckpoints").mockResolvedValue({ checkpoints: [checkpoint("/workspace")], count: 1 });
  const restore = vi.spyOn(typedIPC, "restoreCheckpoint").mockResolvedValue({ result: {
    checkpoint_id: "ckpt1", restored: true, applied_stash: false, restored_untracked: [],
    skipped_existing: [], warnings: [],
  } });
  render(<CheckpointPanel />);
  fireEvent.click(await screen.findByTestId("checkpoint-panel-restore-ckpt1"));
  await waitFor(() => expect(restore).toHaveBeenCalledWith("ckpt1", undefined));
});

it("requires a target and trusted-source confirmation before rebinding", async () => {
  const old = { ...checkpoint("/old"), git_stash_ref: "a".repeat(40) };
  vi.spyOn(typedIPC, "listCheckpoints").mockResolvedValue({ checkpoints: [old], count: 1 });
  const rebind = vi.spyOn(typedIPC, "rebindCheckpoint").mockResolvedValue({ checkpoint: { ...old, source_root: "/new" } });
  render(<CheckpointPanel />);
  await screen.findByTestId("checkpoint-panel-rebind-ckpt1");
  fireEvent.click(screen.getByText("迁移后重新绑定"));
  const button = screen.getByTestId("checkpoint-panel-rebind-ckpt1");
  expect(button).toBeDisabled();
  fireEvent.change(screen.getByLabelText("新仓库根目录"), { target: { value: "/new" } });
  expect(button).toBeDisabled();
  fireEvent.click(screen.getByLabelText("我已核实数据包可信且目标仓库正确"));
  fireEvent.click(button);
  await waitFor(() => expect(rebind).toHaveBeenCalledWith("ckpt1", {
    cwd: "/new", confirm_rebind: true, confirm_trusted_bundle: true,
  }));
  expect(requestConfirmation).toHaveBeenCalledWith(expect.objectContaining({ description: expect.stringContaining("/new") }));
});

it("shows missing snapshot and skipped file details instead of claiming success", async () => {
  vi.spyOn(typedIPC, "listCheckpoints").mockResolvedValue({ checkpoints: [checkpoint("/workspace")], count: 1 });
  vi.spyOn(typedIPC, "restoreCheckpoint").mockResolvedValue({ result: {
    checkpoint_id: "ckpt1", restored: true, applied_stash: true, restored_untracked: [],
    skipped_existing: ["existing.txt"], warnings: ["restore missing.txt: snapshot missing"],
  } });
  render(<CheckpointPanel />);
  fireEvent.click(await screen.findByTestId("checkpoint-panel-restore-ckpt1"));
  expect(await screen.findByText("restore missing.txt: snapshot missing")).toBeInTheDocument();
  expect(screen.getByText("未覆盖已有文件：existing.txt")).toBeInTheDocument();
  expect(toast.error).toHaveBeenCalledWith("恢复未确认完整", expect.any(String));
  expect(toast.success).not.toHaveBeenCalled();
});

it("reports a no-op without claiming that anything was restored", async () => {
  vi.spyOn(typedIPC, "listCheckpoints").mockResolvedValue({ checkpoints: [checkpoint("/workspace")], count: 1 });
  vi.spyOn(typedIPC, "restoreCheckpoint").mockResolvedValue({ result: {
    checkpoint_id: "ckpt1", restored: false, applied_stash: false, restored_untracked: [],
    skipped_existing: [], warnings: [],
  } });
  render(<CheckpointPanel />);
  fireEvent.click(await screen.findByTestId("checkpoint-panel-restore-ckpt1"));
  expect(await screen.findByText(/没有文件被恢复/)).toBeInTheDocument();
  expect(toast.success).not.toHaveBeenCalled();
});

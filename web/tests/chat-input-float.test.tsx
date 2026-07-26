/**
 * Tests for the bottom MessageInput composer.
 *
 * Covers:
 *   - The form is anchored in the main workspace flow, horizontally
 *     centered, with a capped width.
 *   - The always-allow inline toggle (`chat-input-always-allow`)
 *     flips `usePermissionStore.alwaysAllow` on click.
 *   - The inline model picker (`chat-input-model-select`) renders
 *     inside the composer and exposes per-model option testIds.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MessageInput } from "../src/components/chat/MessageInput";
import { useChat, useModelStore, usePermissionStore } from "../src/stores";

const TEST_MODELS = [
  {
    id: "MiniMax-M3",
    name: "MiniMax-M3",
    provider: "MiniMax",
    context_window: 200_000,
    supports_tools: true,
  },
  {
    id: "MiniMax-M3-fast",
    name: "MiniMax-M3-fast",
    provider: "MiniMax",
    context_window: 64_000,
    supports_tools: true,
  },
];

// Mock the typed IPC client so the model store has data to render
// and we don't round-trip to Tauri.
vi.mock("../src/ipc", async () => {
  const actual = await vi.importActual<typeof import("../src/ipc")>("../src/ipc");
  return {
    ...actual,
    typedIPC: {
      ...actual.typedIPC,
      listModels: vi.fn(async () => ({
        models: TEST_MODELS,
        current: "MiniMax-M3",
      })),
      setCurrentModel: vi.fn(async ({ model_id }: { model_id: string }) => ({
        current: model_id,
      })),
    },
  };
});

describe("MessageInput — bottom composer", () => {
  beforeEach(() => {
    useChat.setState({ messages: [], status: "idle", error: null, agentReady: false });
    usePermissionStore.setState({ alwaysAllow: false });
    useModelStore.setState({ models: TEST_MODELS, current: "MiniMax-M3" });
  });

  it("is an in-flow bottom composer with capped width", () => {
    render(<MessageInput />);
    const form = screen.getByTestId("message-input");
    expect(form).toHaveAttribute("data-floating", "false");
    expect(form.className).not.toMatch(/\bfixed\b/);
    expect(form.className).toMatch(/\bshrink-0\b/);
    const inner = form.querySelector("div");
    expect(inner?.className ?? "").toContain("max-w-[780px]");
  });

  it("renders the always-allow inline toggle bound to the store", () => {
    render(<MessageInput />);
    const toggle = screen.getByTestId("chat-input-always-allow");
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(toggle).toHaveTextContent("始终授权");

    fireEvent.click(toggle);
    expect(usePermissionStore.getState().alwaysAllow).toBe(true);
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(toggle).toHaveTextContent("始终授权：开");

    fireEvent.click(toggle);
    expect(usePermissionStore.getState().alwaysAllow).toBe(false);
    expect(toggle).toHaveTextContent("始终授权");
  });

  it("renders the inline model picker inside the composer", async () => {
    render(<MessageInput />);
    // Outer wrapper testId from the parent component
    expect(screen.getByTestId("model-selector")).toBeInTheDocument();
    // The inline trigger testId
    const trigger = await waitFor(() =>
      screen.getByTestId("chat-input-model-select"),
    );
    expect(trigger).toBeInTheDocument();
  });

  it("lists model options with the per-model testId when opened", async () => {
    render(<MessageInput />);
    const trigger = await waitFor(() =>
      screen.getByTestId("chat-input-model-select"),
    );
    fireEvent.click(trigger);
    await waitFor(() => {
      expect(screen.getByTestId("chat-input-model-option-MiniMax-M3")).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("chat-input-model-option-MiniMax-M3-fast"),
    ).toBeInTheDocument();
  });

  it("does not render the legacy footer controls (sanity)", () => {
    render(<MessageInput />);
    // PermissionToggle button (the old footer testId) is gone.
    expect(screen.queryByTestId("permission-toggle")).toBeNull();
  });
});

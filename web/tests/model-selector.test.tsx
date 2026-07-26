/**
 * Tests for the ModelSelector — verifies the dropdown opens, lists
 * models, and triggers a model switch on click.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ModelSelector } from "../src/components/chat/ModelSelector";
import { useModelStore } from "../src/stores";

// Mock the typed IPC client so we don't actually round-trip to Tauri.
vi.mock("../src/ipc", async () => {
  const actual = await vi.importActual<typeof import("../src/ipc")>("../src/ipc");
  return {
    ...actual,
    typedIPC: {
      ...actual.typedIPC,
      listModels: vi.fn(async () => ({
        models: [
          { id: "m1", name: "M1", provider: "p", context_window: 1000, supports_tools: true },
          { id: "m2", name: "M2", provider: "p", context_window: 2000, supports_tools: true },
        ],
        current: "m1",
      })),
      setCurrentModel: vi.fn(async ({ model_id }: { model_id: string }) => ({
        current: model_id,
      })),
    },
  };
});

describe("ModelSelector", () => {
  beforeEach(() => {
    useModelStore.setState({ models: [], current: null });
  });

  it("renders the current model label after the store loads", async () => {
    render(<ModelSelector testId="ms" />);
    await waitFor(() => {
      expect(screen.getByTestId("ms")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(useModelStore.getState().models.length).toBe(2);
    });
    expect(screen.getByText("M1")).toBeInTheDocument();
  });

  it("opens the menu and lists models", async () => {
    render(<ModelSelector />);
    await waitFor(() => {
      expect(useModelStore.getState().models.length).toBe(2);
    });
    fireEvent.click(screen.getByTestId("model-selector-trigger"));
    const menu = screen.getByTestId("model-selector-menu");
    expect(menu).toBeInTheDocument();
    expect(screen.getAllByRole("option")).toHaveLength(2);
    expect(screen.getByTestId("model-selector-search")).toBeInTheDocument();
  });

  it("filters models from the selector search", async () => {
    render(<ModelSelector />);
    await waitFor(() => {
      expect(useModelStore.getState().models.length).toBe(2);
    });
    fireEvent.click(screen.getByTestId("model-selector-trigger"));
    fireEvent.change(screen.getByTestId("model-selector-search"), {
      target: { value: "M2" },
    });
    expect(screen.queryByTestId("chat-input-model-option-m1")).toBeNull();
    expect(screen.getByTestId("chat-input-model-option-m2")).toBeInTheDocument();
  });

  it("calls setCurrentModel when another option is selected", async () => {
    const { typedIPC } = await import("../src/ipc");
    render(<ModelSelector />);
    await waitFor(() => {
      expect(useModelStore.getState().models.length).toBe(2);
    });
    fireEvent.click(screen.getByTestId("model-selector-trigger"));
    const options = screen.getAllByRole("option");
    fireEvent.click(options[1]);
    await waitFor(() => {
      expect(typedIPC.setCurrentModel).toHaveBeenCalledWith("m2");
    });
  });
});

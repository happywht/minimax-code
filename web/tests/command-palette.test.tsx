import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CommandPalette } from "../src/components/layout/CommandPalette";

const noop = () => undefined;

describe("CommandPalette", () => {
  it("opens with Ctrl+K", async () => {
    render(
      <CommandPalette
        onOpenSkills={noop}
        onOpenSettings={noop}
        onTogglePreview={noop}
      />,
    );
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(await screen.findByTestId("command-palette")).toBeInTheDocument();
    expect(screen.getByTestId("command-palette-input")).toBeInTheDocument();
  });

  it("filters items by query", async () => {
    render(
      <CommandPalette
        onOpenSkills={noop}
        onOpenSettings={noop}
        onTogglePreview={noop}
      />,
    );
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const input = await screen.findByTestId("command-palette-input");
    fireEvent.change(input, { target: { value: "模型" } });
    await waitFor(() => {
      expect(screen.getByTestId("command-palette-item-setting:models")).toBeInTheDocument();
    });
  });

  it("calls action callback on Enter", async () => {
    const onTogglePreview = vi.fn();
    render(
      <CommandPalette
        onOpenSkills={noop}
        onOpenSettings={noop}
        onTogglePreview={onTogglePreview}
      />,
    );
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const input = await screen.findByTestId("command-palette-input");
    fireEvent.change(input, { target: { value: "preview" } });
    await waitFor(() => {
      expect(screen.getByTestId("command-palette-item-action:preview")).toBeInTheDocument();
    });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => {
      expect(onTogglePreview).toHaveBeenCalledTimes(1);
    });
  });

  it("closes on Esc via Modal", async () => {
    render(
      <CommandPalette
        onOpenSkills={noop}
        onOpenSettings={noop}
        onTogglePreview={noop}
      />,
    );
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const input = await screen.findByTestId("command-palette-input");
    fireEvent.keyDown(input, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByTestId("command-palette")).not.toBeInTheDocument();
    });
  });
});

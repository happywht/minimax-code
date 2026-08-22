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

  it("opens the data settings tab from the palette", async () => {
    const onOpenSettings = vi.fn();
    render(
      <CommandPalette
        onOpenSkills={noop}
        onOpenSettings={onOpenSettings}
        onTogglePreview={noop}
      />,
    );
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const input = await screen.findByTestId("command-palette-input");
    fireEvent.change(input, { target: { value: "数据" } });
    await waitFor(() => {
      expect(screen.getByTestId("command-palette-item-setting:data")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("command-palette-item-setting:data"));
    await waitFor(() => {
      expect(onOpenSettings).toHaveBeenCalledWith("data");
    });
  });

  it("offers the four governance tabs as setting entries", async () => {
    render(
      <CommandPalette
        onOpenSkills={noop}
        onOpenSettings={noop}
        onTogglePreview={noop}
      />,
    );
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const input = await screen.findByTestId("command-palette-input");
    fireEvent.change(input, { target: { value: "settings" } });
    await waitFor(() => {
      for (const tab of ["mcp-servers", "memory", "plugins", "data"]) {
        expect(screen.getByTestId(`command-palette-item-setting:${tab}`)).toBeInTheDocument();
      }
    });
  });

  it("calls action callback on Enter", async () => {    const onTogglePreview = vi.fn();
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

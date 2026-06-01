/**
 * Tests for the PermissionToggle — the bottom-bar "always allow" switch.
 */
import { describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PermissionToggle } from "../src/components/PermissionToggle";
import { usePermissionStore } from "../src/stores";

describe("PermissionToggle", () => {
  it("renders the off state by default", () => {
    usePermissionStore.setState({ alwaysAllow: false });
    render(<PermissionToggle testId="pt" />);
    const btn = screen.getByTestId("pt");
    expect(btn).toHaveAttribute("aria-checked", "false");
    expect(btn).toHaveTextContent("始终授权");
  });

  it("toggles state on click and updates the store", () => {
    usePermissionStore.setState({ alwaysAllow: false });
    render(<PermissionToggle testId="pt" />);
    const btn = screen.getByTestId("pt");
    fireEvent.click(btn);
    expect(usePermissionStore.getState().alwaysAllow).toBe(true);
    expect(btn).toHaveAttribute("aria-checked", "true");
    expect(btn).toHaveTextContent("始终授权：开");
  });
});

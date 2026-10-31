import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { InspectorTabBar } from "./InspectorTabBar";
import { INSPECTOR_TABS, type InspectorTab } from "./tabs";

describe("InspectorTabBar", () => {
  it("supports keyboard navigation and keeps a selected secondary panel visible", () => {
    function Example() {
      const [active, setActive] = useState<InspectorTab>("timeline");
      return <InspectorTabBar testId="inspector" activeTab={active} onSelect={setActive} />;
    }
    render(<Example />);
    const first = screen.getByRole("tab", { name: INSPECTOR_TABS[0].label });
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowRight" });
    const second = screen.getByRole("tab", { name: INSPECTOR_TABS[1].label });
    expect(second).toHaveFocus();
    expect(second).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByRole("button", { name: "更多面板" }));
    const secondary = screen.getByTestId("inspector-tab-terminal");
    fireEvent.click(secondary);
    fireEvent.click(screen.getByRole("button", { name: "更多面板" }));
    expect(screen.getByTestId("inspector-tab-terminal")).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByTestId("inspector-tab-runner")).not.toBeInTheDocument();
  });
});

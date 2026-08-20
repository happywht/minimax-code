import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { StorageBanner } from "../src/components/layout/StorageBanner";

describe("StorageBanner", () => {
  it("renders nothing when storage is healthy", () => {
    const { container } = render(<StorageBanner degraded={false} />);
    expect(screen.queryByTestId("storage-banner")).toBeNull();
    expect(container.firstChild).toBeNull();
  });

  it("shows a persistent, non-dismissible notice when degraded", () => {
    render(<StorageBanner degraded />);
    const banner = screen.getByTestId("storage-banner");
    expect(banner).toHaveAttribute("role", "status");
    expect(banner).toHaveTextContent("本地存储不可用");
    expect(banner).toHaveTextContent("不会被保存");
    // Persistent by design: no dismiss button or action of any kind.
    expect(banner.querySelector("button")).toBeNull();
  });
});

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ConnectionBanner } from "../src/components/layout/ConnectionBanner";

describe("ConnectionBanner", () => {
  it("shows reconnect status and invokes manual retry", () => {
    const onRetry = vi.fn();
    render(
      <ConnectionBanner
        state="disconnected"
        retryDelayMs={4000}
        onRetry={onRetry}
      />,
    );

    expect(screen.getByTestId("connection-banner")).toHaveTextContent("与 Agent 的连接已断开");
    expect(screen.getByTestId("connection-banner")).toHaveTextContent("4 秒后重试");

    fireEvent.click(screen.getByTestId("connection-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("renders an error state distinctly", () => {
    render(
      <ConnectionBanner
        state="error"
        retryDelayMs={1000}
        onRetry={() => undefined}
      />,
    );

    expect(screen.getByTestId("connection-banner")).toHaveTextContent("连接出错");
    expect(screen.getByTestId("connection-banner")).toHaveTextContent("1 秒后重试");
  });
});

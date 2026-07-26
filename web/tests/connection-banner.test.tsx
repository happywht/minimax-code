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

    expect(screen.getByTestId("connection-banner")).toHaveTextContent("Agent disconnected");
    expect(screen.getByTestId("connection-banner")).toHaveTextContent("Next retry in 4s");

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

    expect(screen.getByTestId("connection-banner")).toHaveTextContent("Connection error");
    expect(screen.getByTestId("connection-banner")).toHaveTextContent("Next retry in 1s");
  });
});

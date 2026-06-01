/**
 * Smoke test for the App component. The IPC client is set to mock
 * mode (no Tauri internals → IPCClient.useMock=true), so the entire
 * tree can render and a message can be sent end-to-end without the
 * Rust shell.
 */
import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "../src/App";

// Make sure no Tauri runtime is detected — IPCClient falls back to
// the in-process mock backend, which emits streaming chunks via
// setTimeout. We use fake timers so the test is deterministic.
beforeAll(() => {
  // jsdom doesn't ship crypto.randomUUID reliably in old versions;
  // provide a polyfill that the IPCClient can call.
  if (typeof globalThis.crypto === "undefined") {
    Object.defineProperty(globalThis, "crypto", {
      value: { randomUUID: () => "test-uuid" },
      configurable: true,
    });
  }
});

describe("App smoke test", () => {
  it("renders the sidebar, footer, and composer", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByTestId("app-root")).toBeInTheDocument();
    });
    expect(screen.getByTestId("sidebar-brand")).toBeInTheDocument();
    expect(screen.getByTestId("chat-panel")).toBeInTheDocument();
    expect(screen.getByTestId("message-input")).toBeInTheDocument();
    expect(screen.getByTestId("model-selector")).toBeInTheDocument();
    expect(screen.getByTestId("permission-toggle")).toBeInTheDocument();
    expect(screen.getByTestId("progress-panel")).toBeInTheDocument();
    expect(screen.getByTestId("user-badge")).toBeInTheDocument();
  });

  it("submits a message on Enter and shows it as a user bubble", async () => {
    const user = userEvent.setup();
    render(<App />);
    const textarea = await waitFor(() =>
      screen.getByTestId("message-input-textarea"),
    );
    await user.type(textarea, "hello{enter}");
    await waitFor(() => {
      expect(screen.getByText("hello")).toBeInTheDocument();
    });
  });
});

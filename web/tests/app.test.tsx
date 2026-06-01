import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "../src/App";

// Mock the Tauri APIs so the component tree can render in a browser.
vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(async (cmd: string) => {
    if (cmd === "ipc_request") {
      return { id: "test-id", method: "ping" };
    }
    if (cmd === "ping_agent") {
      return true;
    }
    return null;
  }),
}));

vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn(async () => () => {}),
}));

describe("App smoke test", () => {
  it("renders the sidebar and composer", async () => {
    // Pretend we're inside a Tauri webview so the connect hint disappears.
    Object.defineProperty(window, "__TAURI_INTERNALS__", {
      value: {},
      configurable: true,
    });
    render(<App />);
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /MiniMax Code/ }),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByPlaceholderText(/Type 'hello'/),
    ).toBeInTheDocument();
  });

  it("submits a message on Enter", async () => {
    Object.defineProperty(window, "__TAURI_INTERNALS__", {
      value: {},
      configurable: true,
    });
    render(<App />);
    const textarea = (await waitFor(() =>
      screen.getByPlaceholderText(/Type 'hello'/),
    )) as HTMLTextAreaElement;
    await userEvent.type(textarea, "hello{enter}");
    // The user's message should appear as a bubble.
    await waitFor(() => {
      expect(screen.getAllByText("hello").length).toBeGreaterThan(0);
    });
  });
});

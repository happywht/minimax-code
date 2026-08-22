/**
 * Smoke test for the App component. The IPC client is set to mock
 * mode (no Tauri internals → IPCClient.useMock=true), so the entire
 * tree can render and a message can be sent end-to-end without the
 * Rust shell.
 */
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "../src/App";
import { useChat, useSessionStore } from "../src/stores";

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
  // This suite asserts mock-backend behaviour. A real agent listening
  // on 127.0.0.1:8765 (e.g. a dev server started next to the tests)
  // would answer the /health probe, flip the IPC client to HTTP mode,
  // and break every mock assumption below (plus spend real LLM calls).
  // Reject all fetches so the probe fails and the client falls back
  // to the in-process mock deterministically.
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.reject(new Error("network disabled in app tests"))),
  );
});

afterAll(() => {
  vi.unstubAllGlobals();
});

describe("App smoke test", () => {
  it("renders the sidebar, footer, and composer", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByTestId("app-root")).toBeInTheDocument();
    });
    expect(screen.getAllByTestId("sidebar-brand")[0]).toBeInTheDocument();
    expect(screen.getAllByTestId("sidebar-brand")).toHaveLength(1);
    expect(screen.getByTestId("chat-panel")).toBeInTheDocument();
    expect(screen.getByTestId("chat-panel")).toHaveClass("min-h-0", "overflow-hidden");
    expect(screen.getByTestId("message-input")).toBeInTheDocument();
    // Always-allow and model picker are now inline in the floating
    // composer — the legacy footer is gone.
    expect(screen.getByTestId("chat-input-always-allow")).toBeInTheDocument();
    expect(screen.getByTestId("chat-input-model-select")).toBeInTheDocument();
    expect(screen.getByTestId("right-panel")).toBeInTheDocument();
    expect(screen.getAllByTestId("user-badge")[0]).toBeInTheDocument();
  });

  it("submits a message on Enter and shows it as a user bubble", async () => {
    const user = userEvent.setup();
    render(<App />);
    const textarea = await waitFor(() =>
      screen.getByTestId("message-input-textarea"),
    );
    await user.type(textarea, "hello{enter}");
    await waitFor(() => {
      expect(screen.getByTestId("message-user")).toHaveTextContent("hello");
    }, { timeout: 10_000 });
    // End-to-end through the mock backend's streaming setTimeouts: the
    // default 5s test timeout can fire before the 10s waitFor under
    // parallel-worker load, so budget the whole test explicitly.
  }, 15_000);

  it("opens shortcuts with ? and closes them with Escape without stealing textarea input", async () => {
    const user = userEvent.setup();
    render(<App />);
    const textarea = await waitFor(() =>
      screen.getByTestId("message-input-textarea"),
    );

    await user.keyboard("?");
    expect(screen.getByTestId("shortcuts-overlay")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByTestId("shortcuts-overlay")).toBeNull();

    await user.click(textarea);
    await user.keyboard("?");
    expect(screen.queryByTestId("shortcuts-overlay")).toBeNull();
    expect(textarea).toHaveValue("?");
  });

  it("opens management views as overlays without unmounting the chat workbench", async () => {
    const user = userEvent.setup();
    render(<App />);
    await waitFor(() => {
      expect(screen.getByTestId("chat-panel")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("sidebar-nav-skills"));
    expect(screen.getByTestId("workspace-overlay")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("skills-panel")).toBeInTheDocument();
    });
    expect(screen.getByTestId("chat-panel")).toBeInTheDocument();

    await user.click(screen.getByTestId("skills-close"));
    expect(screen.queryByTestId("workspace-overlay")).toBeNull();

    await user.click(screen.getByTestId("sidebar-nav-settings"));
    expect(screen.getByTestId("workspace-overlay")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("settings-page")).toBeInTheDocument();
    });
    expect(screen.getByTestId("chat-panel")).toBeInTheDocument();
  });

  it("routes scheduled jobs and agents navigation to their real management tabs", async () => {
    const user = userEvent.setup();
    render(<App />);
    await waitFor(() => {
      expect(screen.getByTestId("chat-panel")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("sidebar-nav-scheduled"));
    expect(await screen.findByTestId("settings-scheduled")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-nav-scheduled")).toHaveAttribute("aria-current", "page");

    await user.click(screen.getByTestId("settings-close"));
    await user.click(screen.getByTestId("sidebar-nav-agents"));
    expect(await screen.findByTestId("settings-agents")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-nav-agents")).toHaveAttribute("aria-current", "page");
  });

  it("opens provider settings directly from the demo-mode status", async () => {
    const user = userEvent.setup();
    render(<App />);

    const banner = await screen.findByTestId("provider-readiness-banner");
    expect(banner).toHaveTextContent("演示模式");
    await user.click(screen.getByTestId("provider-readiness-action"));

    expect(await screen.findByTestId("settings-providers")).toBeInTheDocument();
    expect(screen.getByTestId("settings-tab-providers")).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});

beforeEach(() => {
  window.localStorage.clear();
  useChat.getState().reset();
  useSessionStore.setState({
    sessions: [],
    currentSessionId: null,
    loading: false,
    filter: "all",
  });
});

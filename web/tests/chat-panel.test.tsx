/**
 * Tests for the ChatPanel — header status pill, session title, and
 * the + New button delegates to the session store.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ChatPanel } from "../src/components/chat/ChatPanel";
import { useChat, useSessionStore } from "../src/stores";

vi.mock("../src/ipc", async () => {
  const actual = await vi.importActual<typeof import("../src/ipc")>("../src/ipc");
  return {
    ...actual,
    typedIPC: {
      ...actual.typedIPC,
      listSessions: vi.fn(async () => ({ sessions: [] })),
      listProjects: vi.fn(async () => ({ projects: [] })),
      createSession: vi.fn(async () => ({ session_id: "ses_new" })),
    },
  };
});

describe("ChatPanel", () => {
  beforeEach(() => {
    useChat.setState({ messages: [], status: "idle", error: null, agentReady: false });
    useSessionStore.setState({
      sessions: [
        {
          id: "ses_existing",
          title: "Existing task",
          archived: false,
          created_at: 1,
          updated_at: 1,
          model_id: null,
        },
      ],
      currentSessionId: null,
      loading: false,
      filter: "all",
    });
  });

  it("renders the header with default title and ready status", async () => {
    render(<ChatPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("chat-header-title")).toHaveTextContent("New task");
    });
    expect(screen.getByTestId("chat-header-status")).toHaveTextContent("Ready");
  });

  it("shows the error pill and the error message when status is error", () => {
    useChat.setState({ status: "error", error: "boom" });
    render(<ChatPanel />);
    expect(screen.getByTestId("chat-header-status")).toHaveTextContent("Error");
    expect(screen.getByTestId("chat-header-error")).toHaveTextContent("boom");
  });

  it("shows the streaming label when status is streaming", () => {
    useChat.setState({ status: "streaming" });
    render(<ChatPanel />);
    expect(screen.getByTestId("chat-header-status")).toHaveTextContent("Streaming");
  });

  it("calls session.create when the + New button is clicked", async () => {
    const { typedIPC } = await import("../src/ipc");
    render(<ChatPanel />);
    fireEvent.click(screen.getByTestId("chat-header-new"));
    await waitFor(() => {
      expect(typedIPC.createSession).toHaveBeenCalledWith(
        expect.objectContaining({ title: "New task" }),
      );
    });
  });
});

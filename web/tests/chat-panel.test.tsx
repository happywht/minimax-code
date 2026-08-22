/**
 * Tests for the ChatPanel — header status pill, session title, the
 * + New button delegates to the session store, and the header "more"
 * menu exposes session-level actions (export / archive / delete).
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ChatPanel } from "../src/components/chat/ChatPanel";
import { ConfirmationDialog } from "../src/components/modals/ConfirmationDialog";
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
      archiveSession: vi.fn(async () => ({})),
      deleteSession: vi.fn(async () => ({})),
    },
  };
});

describe("ChatPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
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
      expect(screen.getByTestId("chat-header-title")).toHaveTextContent("新任务");
    });
    expect(screen.getByTestId("chat-header-status")).toHaveTextContent("就绪");
  });

  it("shows the error pill and the error message when status is error", () => {
    useChat.setState({ status: "error", error: "boom" });
    render(<ChatPanel />);
    expect(screen.getByTestId("chat-header-status")).toHaveTextContent("错误");
    expect(screen.getByTestId("chat-header-error")).toHaveTextContent("boom");
  });

  it("shows the streaming label when status is streaming", () => {
    useChat.setState({ status: "streaming" });
    render(<ChatPanel />);
    expect(screen.getByTestId("chat-header-status")).toHaveTextContent("生成中");
  });

  it("calls session.create when the + New button is clicked", async () => {
    const { typedIPC } = await import("../src/ipc");
    render(<ChatPanel />);
    fireEvent.click(screen.getByTestId("chat-header-new"));
    await waitFor(() => {
      expect(typedIPC.createSession).toHaveBeenCalledWith(
        expect.objectContaining({ title: "新任务" }),
      );
    });
  });

  it("opens the more menu with session actions disabled when no session is open", () => {
    render(<ChatPanel />);
    fireEvent.click(screen.getByTestId("chat-header-menu"));
    const menu = screen.getByTestId("chat-header-menu-menu");
    expect(menu).toBeInTheDocument();
    const items = menu.querySelectorAll("button[role='menuitem']");
    expect(items).toHaveLength(3);
    for (const item of items) expect(item).toBeDisabled();
  });

  it("archives the current session from the more menu", async () => {
    const { typedIPC } = await import("../src/ipc");
    useSessionStore.setState({ currentSessionId: "ses_existing" });
    render(<ChatPanel />);
    fireEvent.click(screen.getByTestId("chat-header-menu"));
    fireEvent.click(screen.getByText("归档会话"));
    await waitFor(() => {
      expect(typedIPC.archiveSession).toHaveBeenCalledWith("ses_existing");
    });
    // The optimistic store update flips the session to archived.
    expect(useSessionStore.getState().sessions[0].archived).toBe(true);
  });

  it("deletes the current session after confirmation", async () => {
    const { typedIPC } = await import("../src/ipc");
    useSessionStore.setState({ currentSessionId: "ses_existing" });
    render(
      <>
        <ChatPanel />
        <ConfirmationDialog />
      </>,
    );
    fireEvent.click(screen.getByTestId("chat-header-menu"));
    fireEvent.click(screen.getByText("删除会话"));
    // The confirmation dialog appears; confirming runs the delete.
    const confirm = await screen.findByTestId("confirmation-confirm");
    fireEvent.click(confirm);
    await waitFor(() => {
      expect(typedIPC.deleteSession).toHaveBeenCalledWith("ses_existing");
    });
    expect(useSessionStore.getState().sessions).toHaveLength(0);
  });

  it("keeps the session when the delete confirmation is cancelled", async () => {
    const { typedIPC } = await import("../src/ipc");
    useSessionStore.setState({ currentSessionId: "ses_existing" });
    render(
      <>
        <ChatPanel />
        <ConfirmationDialog />
      </>,
    );
    fireEvent.click(screen.getByTestId("chat-header-menu"));
    fireEvent.click(screen.getByText("删除会话"));
    fireEvent.click(await screen.findByTestId("confirmation-cancel"));
    await waitFor(() => {
      expect(screen.queryByTestId("confirmation-dialog")).not.toBeInTheDocument();
    });
    expect(typedIPC.deleteSession).not.toHaveBeenCalled();
    expect(useSessionStore.getState().sessions).toHaveLength(1);
  });
});

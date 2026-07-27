import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MessageItem } from "../src/components/chat/MessageItem";
import { useChat } from "../src/stores/chat";

const mockMessage = {
  id: "msg_1",
  role: "user" as const,
  text: "Hello",
  streaming: false,
  status: "completed" as const,
  created_at: Date.now(),
};

describe("MessageItem actions", () => {
  beforeEach(() => {
    useChat.setState({
      messages: [mockMessage],
      updateMessage: vi.fn(),
      deleteMessage: vi.fn(),
    });
  });

  it("opens action menu and deletes a message", async () => {
    const { deleteMessage } = useChat.getState();
    render(<MessageItem message={mockMessage} />);
    fireEvent.click(screen.getByTestId("message-actions-msg_1"));
    fireEvent.click(screen.getByTestId("message-delete-msg_1"));
    await waitFor(() => {
      expect(deleteMessage).toHaveBeenCalledWith("msg_1");
    });
  });

  it("enters edit mode for user messages and saves edits", async () => {
    const { updateMessage } = useChat.getState();
    render(<MessageItem message={mockMessage} />);
    fireEvent.click(screen.getByTestId("message-actions-msg_1"));
    fireEvent.click(screen.getByTestId("message-edit-msg_1"));
    const input = await screen.findByTestId("message-edit-input-msg_1");
    expect(input).toBeInTheDocument();

    fireEvent.change(input, { target: { value: "Hello edited" } });
    fireEvent.click(screen.getByTestId("message-edit-save-msg_1"));
    await waitFor(() => {
      expect(updateMessage).toHaveBeenCalledWith("msg_1", "Hello edited");
    });
  });

  it("does not render action menu for system messages", () => {
    const systemMessage = { ...mockMessage, role: "system" as const };
    render(<MessageItem message={systemMessage} />);
    expect(screen.queryByTestId("message-actions-msg_1")).not.toBeInTheDocument();
  });
});

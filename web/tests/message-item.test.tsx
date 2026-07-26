/**
 * Tests for the MessageItem — covers all four roles (user,
 * assistant, tool, system), markdown rendering, and the streaming
 * cursor.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MessageItem } from "../src/components/chat/MessageItem";
import { useChat } from "../src/stores";
import type { Message } from "../src/types/ipc";

const mermaidMock = vi.hoisted(() => ({
  initialize: vi.fn(),
  render: vi.fn(),
}));

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("mermaid", () => ({
  default: mermaidMock,
}));

vi.mock("../src/components/layout/ErrorBoundary", () => ({
  toast: toastMock,
}));

const baseMessage = (overrides: Partial<Message> = {}): Message => ({
  id: "m1",
  role: "user",
  text: "hi",
  streaming: false,
  created_at: Date.now(),
  ...overrides,
});

describe("MessageItem", () => {
  beforeEach(() => {
    mermaidMock.initialize.mockClear();
    mermaidMock.render.mockReset();
    toastMock.success.mockClear();
    toastMock.error.mockClear();
    mermaidMock.render.mockResolvedValue({
      svg: '<svg role="img" aria-label="diagram"><text>diagram</text></svg>',
    });
  });

  it("renders a user bubble with the right-aligned style", () => {
    render(<MessageItem message={baseMessage({ role: "user", text: "hi there" })} />);
    const el = screen.getByTestId("message-user");
    expect(el).toHaveAttribute("data-role", "user");
    expect(screen.getByText("hi there")).toBeInTheDocument();
  });

  it("renders an assistant message with markdown", async () => {
    render(
      <MessageItem
        message={baseMessage({
          role: "assistant",
          text: "Here is `code` and **bold**.",
        })}
      />,
    );
    await waitFor(() => {
      expect(screen.getByText("code")).toBeInTheDocument();
    });
    expect(screen.getByText("bold")).toBeInTheDocument();
  });

  it("copies a full message and surfaces toast feedback", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(
      <MessageItem
        message={baseMessage({
          id: "copy-me",
          role: "assistant",
          text: "Copy this exact message.",
        })}
      />,
    );

    fireEvent.click(screen.getByTestId("message-copy-copy-me"));
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith("Copy this exact message.");
    });
    expect(screen.getByTestId("message-copy-copy-me")).toHaveAttribute("title", "Copy message");
    expect(toastMock.success).toHaveBeenCalledWith("Message copied");
  });

  it("repairs a fenced code block when the language and first line are fused", async () => {
    render(
      <MessageItem
        message={baseMessage({
          role: "assistant",
          text: "```tsconst greet = (name: string) => `Hello, ${name}`;\nconsole.log(greet(\"World\"));\n```",
        })}
      />,
    );
    expect(await screen.findByText("ts")).toBeInTheDocument();
    expect(screen.getByText(/const greet/)).toBeInTheDocument();
    expect(screen.queryByText(/```tsconst/)).toBeNull();
  });

  it("renders code blocks with line numbers and copies the raw code", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(
      <MessageItem
        message={baseMessage({
          role: "assistant",
          text: "```ts\nconst count = 1;\nconsole.log(count);\n```",
        })}
      />,
    );

    expect(await screen.findByTestId("code-line-numbers")).toHaveTextContent("1");
    expect(screen.getByTestId("code-line-numbers")).toHaveTextContent("2");

    fireEvent.click(screen.getByTestId("code-copy-button"));
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith("const count = 1;\nconsole.log(count);");
    });
    expect(screen.getByTestId("code-copy-button")).toHaveTextContent("Copied");
  });

  it("renders compact file reference cards outside fenced code blocks", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(
      <MessageItem
        message={baseMessage({
          role: "assistant",
          text:
            "Review web/src/App.tsx:42 and web/src/App.tsx:42, then compare agent/minimax_code/app.py#12.\n\n" +
            "```ts\nconst hidden = 'web/src/Hidden.tsx:9';\n```",
        })}
      />,
    );

    const cards = await screen.findAllByTestId("file-reference-card");
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveTextContent("App.tsx");
    expect(cards[0]).toHaveTextContent("42");
    expect(cards[1]).toHaveTextContent("app.py");
    expect(cards.map((card) => card.textContent).join(" ")).not.toContain("Hidden.tsx");

    fireEvent.click(screen.getAllByTestId("file-reference-copy")[0]);
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith("web/src/App.tsx:42");
    });
  });

  it("renders inline and block math without parsing math inside code fences", () => {
    const { container } = render(
      <MessageItem
        message={baseMessage({
          role: "assistant",
          text:
            "Inline math $E = mc^2$ stays in the sentence.\n\n" +
            "$$\n\\frac{a}{b}\n$$\n\n" +
            "```md\n$not_math$\n```",
        })}
      />,
    );

    expect(container.querySelector(".katex")).toBeInTheDocument();
    expect(container.querySelector(".katex-display")).toBeInTheDocument();
    expect(screen.getByText("$not_math$")).toBeInTheDocument();
  });

  it("renders mermaid code fences as diagrams with source copy", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(
      <MessageItem
        message={baseMessage({
          role: "assistant",
          text: "```mermaid\ngraph TD\nA-->B\n```",
        })}
      />,
    );

    expect(screen.getByTestId("mermaid-loading")).toBeInTheDocument();
    expect(await screen.findByTestId("mermaid-svg")).toHaveTextContent("diagram");
    expect(screen.queryByTestId("code-line-numbers")).toBeNull();
    expect(mermaidMock.initialize).toHaveBeenCalledWith(
      expect.objectContaining({ startOnLoad: false, securityLevel: "strict" }),
    );
    expect(mermaidMock.render).toHaveBeenCalledWith(expect.any(String), "graph TD\nA-->B");

    fireEvent.click(screen.getByTestId("mermaid-copy-button"));
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith("graph TD\nA-->B");
    });
  });

  it("shows an inline error when a mermaid diagram cannot render", async () => {
    mermaidMock.render.mockRejectedValueOnce(new Error("bad diagram"));

    render(
      <MessageItem
        message={baseMessage({
          role: "assistant",
          text: "```mermaid\ngraph TD\nbroken\n```",
        })}
      />,
    );

    expect(await screen.findByTestId("mermaid-error")).toHaveTextContent("bad diagram");
  });

  it("renders a tool bubble that expands on click", () => {
    const tool: Message = baseMessage({
      id: "t1",
      role: "tool",
      text: "first line\nsecond line",
      tool_name: "read_file",
      tool_args: { path: "x.py" },
    });
    render(<MessageItem message={tool} />);
    const trigger = screen.getByRole("button", { expanded: false });
    expect(trigger).toHaveTextContent(/read_file/);
    fireEvent.click(trigger);
    expect(screen.getByText(/first line/)).toBeInTheDocument();
  });

  it("renders a system message in the error style", () => {
    render(
      <MessageItem
        message={baseMessage({
          role: "system",
          text: "Something broke",
        })}
      />,
    );
    const el = screen.getByTestId("message-system");
    expect(el).toHaveAttribute("data-role", "system");
    expect(el.querySelector("div")?.className).toContain("text-status-error");
  });

  it("shows the streaming cursor when streaming is true (assistant)", () => {
    render(
      <MessageItem
        message={baseMessage({
          role: "assistant",
          text: "partial…",
          streaming: true,
        })}
      />,
    );
    expect(screen.getByText("▍")).toBeInTheDocument();
  });

  it("renders a queued assistant message as a skeleton", () => {
    render(
      <MessageItem
        message={baseMessage({
          id: "a-waiting",
          role: "assistant",
          text: "",
          streaming: true,
          status: "queued",
        })}
      />,
    );
    expect(screen.getByTestId("message-status-a-waiting")).toHaveTextContent("Waiting");
    expect(screen.getByTestId("message-skeleton-a-waiting")).toBeInTheDocument();
  });

  it("renders a failed assistant message with retry", () => {
    const retrySpy = vi.spyOn(useChat.getState(), "retryMessage").mockResolvedValue(undefined);
    render(
      <MessageItem
        message={baseMessage({
          id: "a-failed",
          role: "assistant",
          text: "network down",
          streaming: false,
          status: "failed",
          error: "network down",
          retry_content: "try again",
        })}
      />,
    );
    expect(screen.getByTestId("message-status-a-failed")).toHaveTextContent("Failed");
    fireEvent.click(screen.getByTestId("message-retry-a-failed"));
    expect(retrySpy).toHaveBeenCalledWith("a-failed");
    retrySpy.mockRestore();
  });
});

/**
 * Tests for ContextIndicator — the thin progress bar that shows
 * how much of the model's context window is consumed.
 *
 * Coverage:
 *   1. Renders nothing when no messages and no model data
 *   2. Renders progress bar with correct text when messages have tokens_in
 *   3. Green color when usage < 70%
 *   4. Amber color when usage between 70–90%
 *   5. Red color when usage > 90%
 *   6. "k" suffix for thousands, "M" for millions
 *   7. "used/total" format when both values present
 *   8. Shows only used value when no context_window
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ContextIndicator } from "../ContextIndicator";
import { useChat } from "../../../stores/chat";
import { useModelStore } from "../../../stores/modelStore";
import type { Message } from "../../../types/ipc";

/* ────────── Helpers ────────── */

/** Build an assistant message with a given usage footprint. */
function assistantMsg(id: string, tokensIn: number, tokensOut = 0): Message {
  return {
    id,
    role: "assistant",
    text: "irrelevant",
    streaming: false,
    created_at: Date.now(),
    metadata: { thinking_count: 0, tokens_in: tokensIn, tokens_out: tokensOut },
  };
}

/** Build a user message (no tokens_in metadata). */
function userMsg(id: string): Message {
  return {
    id,
    role: "user",
    text: "hello",
    streaming: false,
    created_at: Date.now(),
  };
}

/**
 * Seed both stores with the provided state and render the component.
 * Returns the rendered container.
 */
function renderWith(messages: Message[], currentModelId: string | null, contextWindow: number) {
  const modelId = contextWindow > 0 ? (currentModelId ?? "model-1") : currentModelId;

  useChat.setState({ messages } as Parameters<typeof useChat.setState>[0]);
  useModelStore.setState({
    models: modelId
      ? [{ id: modelId, name: "Test Model", provider: "test", context_window: contextWindow, supports_tools: false }]
      : [],
    current: modelId,
  } as Parameters<typeof useModelStore.setState>[0]);

  return render(<ContextIndicator />);
}

/* ────────── Setup / Teardown ────────── */

beforeEach(() => {
  // Reset stores to a clean baseline.
  useChat.setState({
    messages: [],
    status: "idle",
    error: null,
    agentReady: false,
  });
  useModelStore.setState({
    models: [],
    current: null,
    loading: false,
  });
});

afterEach(() => {
  cleanup();
});

/* ────────── Test Cases ────────── */

describe("ContextIndicator", () => {
  it("renders nothing when no messages and no model data", () => {
    const { container } = renderWith([], null, 0);
    expect(container.innerHTML).toBe("");
  });

  it("renders progress bar with correct text when messages have tokens_in", () => {
    // 5000 tokens used, 200k context window → 2.5% → green bar
    renderWith([assistantMsg("a1", 5000)], "model-1", 200_000);
    expect(screen.getByText("5.0k/200.0k")).toBeInTheDocument();
  });

  it("uses green color when usage < 70%", () => {
    // 100k tokens used, 200k context window → 50% → green
    renderWith([assistantMsg("a1", 100_000)], "model-1", 200_000);
    const label = screen.getByText("100.0k/200.0k");
    // The <span> carries the text color class.
    expect(label.className).toContain("text-status-success");
    // The progress bar carries the bg color class.
    // It is the only element with an inline style (width: N%).
    const bar = label.parentElement!.querySelector("div[style]");
    expect(bar?.className).toContain("bg-emerald-400");
  });

  it("uses amber color when usage between 70% and 90%", () => {
    // 150k tokens used, 200k context window → 75% → amber
    renderWith([assistantMsg("a1", 150_000)], "model-1", 200_000);
    const label = screen.getByText("150.0k/200.0k");
    expect(label.className).toContain("text-status-warning");
    const bar = label.parentElement!.querySelector("div[style]");
    expect(bar?.className).toContain("bg-amber-400");
  });

  it("uses red color when usage > 90%", () => {
    // 190k tokens used, 200k context window → 95% → red
    renderWith([assistantMsg("a1", 190_000)], "model-1", 200_000);
    const label = screen.getByText("190.0k/200.0k");
    expect(label.className).toContain("text-status-error");
    const bar = label.parentElement!.querySelector("div[style]");
    expect(bar?.className).toContain("bg-red-400");
  });

  it("formats tokens with k suffix for thousands", () => {
    // 1234 tokens → "1.2k"
    renderWith([assistantMsg("a1", 1234)], "model-1", 0);
    expect(screen.getByText("1.2k")).toBeInTheDocument();
  });

  it("formats tokens with M suffix for millions", () => {
    // 1_500_000 tokens → "1.5M"
    renderWith([assistantMsg("a1", 1_500_000)], "model-1", 0);
    expect(screen.getByText("1.5M")).toBeInTheDocument();
  });

  it("shows used/total format when both values are present", () => {
    // 12345 tokens used, 100k window → "12.3k/100.0k"
    renderWith([assistantMsg("a1", 12_345)], "model-1", 100_000);
    expect(screen.getByText("12.3k/100.0k")).toBeInTheDocument();
  });

  it("shows only used value when no context_window", () => {
    // Tokens used but no context_window set on the model.
    renderWith([assistantMsg("a1", 42)], "model-1", 0);
    // "42" (plain number, no suffix) and no "/" separator.
    expect(screen.getByText("42")).toBeInTheDocument();
    // Ensure no "/" is present in the label text.
    const label = screen.getByText("42");
    expect(label.textContent).not.toContain("/");
  });

  it("renders nothing when only user messages exist and no model is selected", () => {
    // User messages have no tokens_in metadata, and no model is configured.
    // The component returns null: total=0, used=0.
    const { container } = renderWith([userMsg("u1"), userMsg("u2")], null, 0);
    expect(container.innerHTML).toBe("");
  });

  it("uses the latest assistant footprint instead of summing turns", () => {
    // tokens_in already contains the whole history when the model saw it,
    // so summing turns counts the history once per turn. Only the LAST
    // assistant message's footprint is the current context size.
    renderWith(
      [assistantMsg("a1", 3000), userMsg("u1"), assistantMsg("a2", 7000)],
      "model-1",
      100_000,
    );
    // 7000 / 100000 = 7% → green — NOT 3000+7000=10k.
    expect(screen.getByText("7.0k/100.0k")).toBeInTheDocument();
    expect(screen.queryByText("10.0k/100.0k")).not.toBeInTheDocument();
  });

  it("adds the latest turn's tokens_out to the footprint", () => {
    // The footprint is tokens_in + tokens_out of the latest assistant
    // message: what that call saw plus what it produced — i.e. the
    // context size going into the next turn.
    renderWith([assistantMsg("a1", 6000, 1000)], "model-1", 100_000);
    expect(screen.getByText("7.0k/100.0k")).toBeInTheDocument();
  });
});

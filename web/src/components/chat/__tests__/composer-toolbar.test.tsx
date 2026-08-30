/**
 * ComposerToolbar counter semantics tests (v1.8.0 R3) — the char
 * counter `0/8000` is opaque on its own; the walkthrough finding was
 * that neither the visible label nor its tooltip explains what is
 * being counted. Both must now carry the localized explanation.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ComposerToolbar } from "../ComposerToolbar";
import { MAX_INPUT_CHARS } from "../constants";
import { strings } from "../../../ui/strings";

vi.mock("../../../stores", () => ({
  usePermissionStore: vi.fn(() => ({ alwaysAllow: false, setAlwaysAllow: vi.fn() })),
  useSessionStore: vi.fn(() => ({ currentSessionId: "s1" })),
}));

// The toolbar composes two IPC-backed widgets; both are covered by
// their own suites — stub them so this file only exercises the strip.
vi.mock("../ContextIndicator", () => ({
  ContextIndicator: () => <div data-testid="context-indicator-stub" />,
}));
vi.mock("../ModelSelector", () => ({
  ModelSelector: () => <div data-testid="model-selector-stub" />,
}));

const PROPS = {
  valueLength: 42,
  overLimit: false,
  usagePct: 0,
  nearLimit: false,
  critical: false,
  cancelling: false,
} as const;

describe("ComposerToolbar counter semantics (v1.8.0 R3)", () => {
  it("counter shows current/max and carries the explaining title", () => {
    render(<ComposerToolbar {...PROPS} />);
    const counter = screen.getByTestId("message-input-token-count");
    expect(counter.textContent).toBe(`42/${MAX_INPUT_CHARS}`);
    const expected = strings.chat.composer.charCount(42, MAX_INPUT_CHARS);
    expect(counter.getAttribute("title")).toBe(expected);
    expect(counter.getAttribute("aria-label")).toBe(expected);
  });
});

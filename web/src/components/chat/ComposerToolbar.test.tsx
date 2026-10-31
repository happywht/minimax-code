import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { usePermissionStore, useSessionStore } from "../../stores";
import { ComposerSafetyControls, ComposerToolbar } from "./ComposerToolbar";

vi.mock("./ModelSelector", () => ({ ModelSelector: () => null }));
vi.mock("./ContextIndicator", () => ({ ContextIndicator: () => null }));

afterEach(() => { cleanup(); usePermissionStore.setState({ alwaysAllow: false }); useSessionStore.setState({ newSessionEngine: "native" }); });

it("keeps enabled automatic approval visible and lets the user turn it off", () => {
  usePermissionStore.setState({ alwaysAllow: true });
  render(<ComposerToolbar valueLength={0} overLimit={false} usagePct={0} nearLimit={false} critical={false} cancelling={false} />);
  fireEvent.click(screen.getByRole("switch", { name: "始终授权已开启 · 关闭" }));
  expect(usePermissionStore.getState().alwaysAllow).toBe(false);
  expect(screen.queryByRole("switch", { name: "始终授权已开启 · 关闭" })).not.toBeInTheDocument();
});


it("uses the selected engine for a new session before enabling native-only approval", () => {
  useSessionStore.setState({ currentSessionId: null, newSessionEngine: "codex" });
  render(<ComposerSafetyControls />);
  expect(screen.getByRole("switch", { name: "始终授权" })).toBeDisabled();
});

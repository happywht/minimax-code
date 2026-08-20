/**
 * Tests for P0#4 — focus trap on modal components.
 * Verifies:
 *   1. useFocusTrap hook traps Tab/Shift+Tab within container
 *   2. Focus moves into container when activated
 *   3. Focus restores to trigger when deactivated
 *   4. All 3 modals have role="dialog" and aria-modal="true"
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef, useState } from "react";
import { useFocusTrap } from "../../lib/useFocusTrap";

// ─── Hook unit tests ────────────────────────────────────────────────

function FocusTrapTestComponent({ active }: { active: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, active);
  return (
    <div ref={ref} data-testid="trap-container">
      <button data-testid="btn-first">First</button>
      <button data-testid="btn-second">Second</button>
      <button data-testid="btn-last">Last</button>
    </div>
  );
}

function FocusTrapWithHidden({ active }: { active: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, active);
  return (
    <div ref={ref} data-testid="trap-hidden">
      <button data-testid="btn-hidden-attr" hidden>
        HiddenAttr
      </button>
      <button data-testid="btn-aria-hidden" aria-hidden="true">
        AriaHidden
      </button>
      <button data-testid="btn-visible">Visible</button>
      <button data-testid="btn-last">Last</button>
    </div>
  );
}

function FocusTrapWithAutofocus({ active }: { active: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, active);
  return (
    <div ref={ref}>
      <button data-testid="btn-first">First</button>
      <button data-testid="btn-autofocus" data-autofocus={true}>
        Autofocus
      </button>
      <button data-testid="btn-last">Last</button>
    </div>
  );
}

function FocusTrapWithContentEditable({ active }: { active: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, active);
  return (
    <div ref={ref}>
      <button data-testid="btn-first">First</button>
      <div data-testid="editor" contentEditable={true} suppressContentEditableWarning>
        editable
      </div>
      <button data-testid="btn-last">Last</button>
    </div>
  );
}

describe("useFocusTrap hook", () => {
  it("moves focus to first focusable child when activated", () => {
    render(<FocusTrapTestComponent active={true} />);
    const first = screen.getByTestId("btn-first");
    expect(first).toHaveFocus();
  });

  it("wraps Tab from last to first element", async () => {
    const user = userEvent.setup();
    render(<FocusTrapTestComponent active={true} />);

    const first = screen.getByTestId("btn-first");
    const last = screen.getByTestId("btn-last");

    // Tab from first → second
    await user.tab();
    expect(screen.getByTestId("btn-second")).toHaveFocus();

    // Tab from second → last
    await user.tab();
    expect(last).toHaveFocus();

    // Tab from last → wraps to first
    await user.tab();
    expect(first).toHaveFocus();
  });

  it("wraps Shift+Tab from first to last element", async () => {
    const user = userEvent.setup();
    render(<FocusTrapTestComponent active={true} />);

    const _first = screen.getByTestId("btn-first");
    void _first;
    const last = screen.getByTestId("btn-last");

    // Focus starts on first; Shift+Tab should wrap to last
    await user.tab({ shift: true });
    expect(last).toHaveFocus();
  });

  it("restores focus to trigger element on deactivation", async () => {
    function Wrapper() {
      const [show, setShow] = useState(false);
      return (
        <div>
          <button data-testid="trigger" onClick={() => setShow(true)}>Open</button>
          {show && (
            <>
              <button data-testid="close" onClick={() => setShow(false)}>Close</button>
              <FocusTrapTestComponent active={true} />
            </>
          )}
        </div>
      );
    }
    const user = userEvent.setup();
    render(<Wrapper />);

    const trigger = screen.getByTestId("trigger");
    trigger.focus();
    expect(trigger).toHaveFocus();

    // Open the trap — focus moves inside
    await user.click(trigger);
    expect(screen.getByTestId("btn-first")).toHaveFocus();

    // Close — focus should return to trigger
    await user.click(screen.getByTestId("close"));
    expect(trigger).toHaveFocus();
  });

  it("keeps Shift+Tab inside when focus sits on the container itself", async () => {
    const user = userEvent.setup();
    render(
      <div>
        <button data-testid="outside">Outside</button>
        <FocusTrapTestComponent active={true} />,
      </div>,
    );

    // Simulate focus resting on the container element itself — a bare
    // Shift+Tab would otherwise walk out into the background page.
    const container = screen.getByTestId("trap-container");
    container.setAttribute("tabindex", "-1");
    container.focus();
    expect(container).toHaveFocus();

    await user.tab({ shift: true });
    expect(screen.getByTestId("btn-last")).toHaveFocus();
  });

  it("pulls focus back after it escapes the container", async () => {
    const user = userEvent.setup();
    render(
      <div>
        <button data-testid="outside">Outside</button>
        <FocusTrapTestComponent active={true} />
      </div>,
    );

    // Simulate programmatic focus outside the trap (e.g. a stray
    // ref.focus() elsewhere in the app).
    screen.getByTestId("outside").focus();
    expect(screen.getByTestId("outside")).toHaveFocus();

    // The trap must recapture on the next Tab / Shift+Tab
    await user.tab();
    expect(screen.getByTestId("btn-first")).toHaveFocus();

    screen.getByTestId("outside").focus();
    await user.tab({ shift: true });
    expect(screen.getByTestId("btn-last")).toHaveFocus();
  });

  it("skips hidden and aria-hidden elements in the focus cycle", async () => {
    const user = userEvent.setup();
    render(
      <div ref={undefined}>
        <FocusTrapWithHidden active={true} />
      </div>,
    );

    // Initial focus lands on the first *visible* focusable element
    expect(screen.getByTestId("btn-visible")).toHaveFocus();

    await user.tab();
    expect(screen.getByTestId("btn-last")).toHaveFocus();
  });

  it("prefers a [data-autofocus] child for the initial focus", () => {
    render(<FocusTrapWithAutofocus active={true} />);
    expect(screen.getByTestId("btn-autofocus")).toHaveFocus();
  });

  it("includes contenteditable elements in the focus cycle", async () => {
    const user = userEvent.setup();
    render(<FocusTrapWithContentEditable active={true} />);

    expect(screen.getByTestId("btn-first")).toHaveFocus();
    await user.tab();
    expect(screen.getByTestId("editor")).toHaveFocus();
    await user.tab();
    expect(screen.getByTestId("btn-last")).toHaveFocus();
  });
});

// ─── Modal accessibility attribute tests ────────────────────────────

// Mock stores for modal rendering
vi.mock("../../stores", () => ({
  usePermissionStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({
      pending: { "req-1": { request_id: "req-1", tool: "bash", args: { cmd: "ls" }, received_at: 1 } },
      resolve: vi.fn(),
    }),
  useGitStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({
      lastDiff: null, lastLog: null, loading: false,
      fetchDiff: vi.fn(), fetchLog: vi.fn(),
    }),
}));

vi.mock("../../stores/mobileStore", () => ({
  useMobileStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({
      devices: [], pairingToken: null, qrPayload: null, expiresAt: null,
      loading: false, error: null, startPairing: vi.fn(), fetchDevices: vi.fn(),
      fetchDeviceStatus: vi.fn(), unpair: vi.fn(), clearPairing: vi.fn(), pushNotification: vi.fn(),
    }),
}));

vi.mock("../../types/ipc", () => ({}));
vi.mock("../../components/layout/ErrorBoundary", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import { PermissionRequestModal } from "../../components/modals/PermissionRequestModal";
import { GitViewerModal } from "../../components/modals/GitViewerModal";
import { MobilePairingModal } from "../../components/modals/MobilePairingModal";

describe("Modal accessibility (P0#4)", () => {
  it("PermissionRequestModal has role=dialog, aria-modal, and focus trap", () => {
    const { container } = render(<PermissionRequestModal />);
    const dialog = container.querySelector('[role="dialog"]');
    expect(dialog).toBeTruthy();
    expect(dialog?.getAttribute("aria-modal")).toBe("true");
    // Focus should be inside the dialog
    const focused = document.activeElement;
    expect(dialog?.contains(focused)).toBe(true);
  });

  it("GitViewerModal has role=dialog, aria-modal, and focus trap", () => {
    const { container } = render(<GitViewerModal open={true} onClose={vi.fn()} />);
    const dialog = container.querySelector('[role="dialog"]');
    expect(dialog).toBeTruthy();
    expect(dialog?.getAttribute("aria-modal")).toBe("true");
    const focused = document.activeElement;
    expect(dialog?.contains(focused)).toBe(true);
  });

  it("MobilePairingModal has role=dialog, aria-modal, and focus trap", () => {
    const { container } = render(<MobilePairingModal onClose={vi.fn()} />);
    const dialog = container.querySelector('[role="dialog"]');
    expect(dialog).toBeTruthy();
    expect(dialog?.getAttribute("aria-modal")).toBe("true");
    const focused = document.activeElement;
    expect(dialog?.contains(focused)).toBe(true);
  });
});

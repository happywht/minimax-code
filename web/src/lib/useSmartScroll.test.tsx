import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useSmartScroll } from "./useSmartScroll";

describe("useSmartScroll", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("does not observe or auto-scroll an empty state, and resumes for messages", () => {
    const observe = vi.fn();
    const disconnect = vi.fn();
    vi.stubGlobal("ResizeObserver", class { observe = observe; disconnect = disconnect; });
    function List({ enabled }: { enabled: boolean }) {
      const { containerRef } = useSmartScroll({ contentKey: enabled ? "message" : "", enabled });
      return <div ref={containerRef}><div>content</div></div>;
    }
    const view = render(<List enabled={false} />);
    expect(observe).not.toHaveBeenCalled();
    view.rerender(<List enabled />);
    expect(observe).toHaveBeenCalled();
    view.unmount();
    expect(disconnect).toHaveBeenCalled();
  });
});

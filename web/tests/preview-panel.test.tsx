import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { PreviewPanel } from "../src/components/panels/PreviewPanel";
import { usePreviewStore } from "../src/stores/previewStore";

class FakeEventSource {
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  close = vi.fn();
}

function response(ok: boolean, status: number, body: object = {}) {
  return {
    ok,
    status,
    json: vi.fn(async () => body),
  } as unknown as Response;
}

beforeEach(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
  usePreviewStore.setState({
    url: "http://preview.test",
    filePath: "index.html",
    connected: false,
    reloadCounter: 0,
  });
});

describe("PreviewPanel", () => {
  it("loads the iframe after health and file checks pass", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce(response(true, 200))
        .mockResolvedValueOnce(response(true, 200)),
    );

    render(<PreviewPanel onClose={vi.fn()} />);

    const iframe = await screen.findByTestId("preview-iframe");
    expect(iframe).toHaveAttribute("src", "http://preview.test/preview/index.html");
    expect(screen.queryByTestId("preview-error")).toBeNull();
  });

  it("shows a recoverable error instead of mounting a broken iframe", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce(response(true, 200))
        .mockResolvedValueOnce(response(false, 404, { error: "not found", path: "index.html" })),
    );

    render(<PreviewPanel onClose={vi.fn()} />);

    expect(await screen.findByTestId("preview-error")).toHaveTextContent(
      "Preview file not found: index.html",
    );
    expect(screen.getByTestId("preview-retry-btn")).toBeInTheDocument();
    expect(screen.queryByTestId("preview-iframe")).toBeNull();
  });
});

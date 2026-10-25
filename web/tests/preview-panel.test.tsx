import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { PreviewPanel } from "../src/components/panels/PreviewPanel";
import { usePreviewStore } from "../src/stores/previewStore";
import { useSessionStore } from "../src/stores";

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
  useSessionStore.setState({ currentSessionId: null });
  vi.stubGlobal("EventSource", FakeEventSource);
  usePreviewStore.setState({
    url: "http://preview.test",
    filePath: "index.html",
    connected: false,
    reloadCounter: 0,
  });
});

describe("PreviewPanel", () => {
  it("binds preview and relative assets to the selected session", async () => {
    useSessionStore.setState({ currentSessionId: "session-project-a" });
    const fetch = vi.fn().mockImplementation(async (url: string) =>
      response(true, 200, url.endsWith("/preview/access") ? { path: "/preview/view/fixture" } : {}));
    vi.stubGlobal("fetch", fetch);
    render(<PreviewPanel onClose={vi.fn()} />);
    const iframe = await screen.findByTestId("preview-iframe");
    expect(iframe).toHaveAttribute("src", "http://preview.test/preview/view/fixture/index.html");
    expect(fetch).toHaveBeenCalledWith("http://preview.test/preview/access", expect.objectContaining({
      body: JSON.stringify({ session_id: "session-project-a" }),
    }));
    expect(iframe.getAttribute("sandbox")).not.toContain("allow-same-origin");
  });
  it("loads the iframe after health and file checks pass", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce(response(true, 200))
        .mockResolvedValueOnce(response(true, 200))
        .mockResolvedValueOnce(response(true, 200, { path: "/preview/view/fixture" }))
        .mockResolvedValueOnce(response(true, 200)),
    );

    render(<PreviewPanel onClose={vi.fn()} />);

    const iframe = await screen.findByTestId("preview-iframe");
    expect(iframe).toHaveAttribute("src", "http://preview.test/preview/view/fixture/index.html");
    expect(screen.queryByTestId("preview-error")).toBeNull();
  });

  it("shows a recoverable error instead of mounting a broken iframe", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce(response(true, 200))
        .mockResolvedValueOnce(response(true, 200))
        .mockResolvedValueOnce(response(true, 200, { path: "/preview/view/fixture" }))
        .mockResolvedValueOnce(response(false, 404, { error: "not found", path: "index.html" })),
    );

    render(<PreviewPanel onClose={vi.fn()} />);

    expect(await screen.findByTestId("preview-error")).toHaveTextContent(
      "预览文件不存在：index.html",
    );
    expect(screen.getByTestId("preview-retry-btn")).toBeInTheDocument();
    expect(screen.queryByTestId("preview-iframe")).toBeNull();
  });

  it("authenticates a standalone preview before opening its iframe or event stream", async () => {
    const events = vi.fn().mockImplementation(() => new FakeEventSource());
    vi.stubGlobal("EventSource", events);
    let release: (value: Response) => void = () => {};
    const auth = new Promise<Response>((resolve) => { release = resolve; });
    const fetch = vi.fn()
      .mockResolvedValueOnce(response(true, 200, { auth_required: true }))
      .mockReturnValueOnce(auth)
      .mockResolvedValueOnce(response(true, 200))
      .mockResolvedValueOnce(response(true, 200, { path: "/preview/view/fixture" }))
      .mockResolvedValue(response(true, 200));
    vi.stubGlobal("fetch", fetch);
    render(<PreviewPanel onClose={vi.fn()} />);
    await screen.findByTestId("preview-loading");
    expect(events).not.toHaveBeenCalled();
    expect(screen.queryByTestId("preview-iframe")).toBeNull();
    release(response(true, 200, { token: "preview-test-token" }));
    await screen.findByTestId("preview-iframe");
    expect(fetch).toHaveBeenCalledWith("http://preview.test/auth/session", expect.objectContaining({
      method: "POST", credentials: "include",
    }));
    await waitFor(() => expect(events).toHaveBeenCalledWith("http://preview.test/preview/view/fixture/events"));
  });
});

/**
 * Tests for PreviewPanel — CORS probe behaviour.
 *
 * v1.7.1: the health/file probes must bypass the HTTP cache. The iframe
 * navigation to the same URL stores a response without CORS headers
 * (navigations send no ``Origin``), and a later cache-hit for the probe
 * fetch is rejected as a CORS failure — "Failed to fetch" forever after
 * the first render. These tests pin ``cache: "no-store"`` on both probes
 * and the localized network-failure copy.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { PreviewPanel } from "../PreviewPanel";
import { usePreviewStore } from "../../../stores";
import { strings } from "../../../ui/strings";

// jsdom has no EventSource implementation — stub the SSE hookup.
class FakeEventSource {
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  constructor(_url: string) {}
  close() {}
}

const fetchMock = vi.fn();

const okResponse = () =>
  Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) } as Response);

beforeEach(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  usePreviewStore.setState({
    url: "http://127.0.0.1:8765",
    filePath: "index.html",
    connected: false,
    reloadCounter: 0,
    rootProjectId: null,
    rootWorkspace: "",
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PreviewPanel CORS probes", () => {
  it("sends both health and file probes with cache: no-store", async () => {
    fetchMock.mockImplementation(okResponse);
    render(<PreviewPanel onClose={() => {}} />);

    await waitFor(() => {
      expect(screen.getByTestId("preview-iframe")).toBeTruthy();
    });

    const initArgs = fetchMock.mock.calls
      .filter(([u]) => String(u).includes("/preview/"))
      .map(([, init]) => init);
    expect(initArgs.length).toBeGreaterThanOrEqual(2);
    for (const init of initArgs) {
      expect((init as RequestInit | undefined)?.cache).toBe("no-store");
    }
  });

  it("keeps bypassing the cache after the iframe has loaded once (re-root reload)", async () => {
    fetchMock.mockImplementation(okResponse);
    render(<PreviewPanel onClose={() => {}} />);
    await waitFor(() => {
      expect(screen.getByTestId("preview-iframe")).toBeTruthy();
    });

    // Simulate a project re-root: bump reloadCounter → checkPreview runs again.
    fetchMock.mockClear();
    usePreviewStore.setState({ reloadCounter: 1, rootProjectId: "pB" });
    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2);
    });
    const caches = fetchMock.mock.calls.map(
      ([, init]) => (init as RequestInit | undefined)?.cache,
    );
    expect(caches.length).toBeGreaterThanOrEqual(2);
    for (const c of caches) expect(c).toBe("no-store");
  });

  it("shows the localized unreachable hint instead of the raw browser error", async () => {
    fetchMock.mockImplementation((_url: string, _init?: RequestInit) => {
      // Health probe passes; the file probe hits the poisoned/absent server.
      if (String(_url).endsWith("/preview/health")) return okResponse();
      // A cache-hit CORS rejection surfaces as a TypeError, exactly like Chrome.
      return Promise.reject(new TypeError("Failed to fetch"));
    });
    render(<PreviewPanel onClose={() => {}} />);

    await waitFor(() => {
      expect(screen.getByTestId("preview-error")).toBeTruthy();
    });
    expect(screen.getByText(strings.panels.preview.unreachable)).toBeTruthy();
    expect(screen.queryByText(/Failed to fetch/)).toBeNull();
  });
});

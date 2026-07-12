/**
 * PreviewPanel — full-width live-preview iframe with SSE hot-reload.
 *
 * Layout:
 *   ┌──────────────────────────────────────────────────────────┐
 *   │ [path input] [⟳ Reload] [Connected●] [✕ Close]          │
 *   ├──────────────────────────────────────────────────────────┤
 *   │                                                          │
 *   │              <iframe src={previewUrl} />                  │
 *   │                                                          │
 *   └──────────────────────────────────────────────────────────┘
 *
 * SSE events from the preview server trigger an automatic iframe
 * reload. The user can also manually reload via the toolbar button.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, RefreshCw, TriangleAlert, X, Wifi, WifiOff } from "lucide-react";
import { usePreviewStore } from "../stores/previewStore";

export interface PreviewPanelProps {
  onClose: () => void;
}

export function PreviewPanel({ onClose }: PreviewPanelProps): JSX.Element {
  const url = usePreviewStore((s) => s.url);
  const filePath = usePreviewStore((s) => s.filePath);
  const connected = usePreviewStore((s) => s.connected);
  const reloadCounter = usePreviewStore((s) => s.reloadCounter);
  const setFilePath = usePreviewStore((s) => s.setFilePath);
  const setConnected = usePreviewStore((s) => s.setConnected);
  const reload = usePreviewStore((s) => s.reload);

  const eventSourceRef = useRef<EventSource | null>(null);
  const [previewState, setPreviewState] = useState<"checking" | "ready" | "error">("checking");
  const [previewError, setPreviewError] = useState<string | null>(null);

  // Build the full preview URL.
  const encodedPath = filePath
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
  const previewUrl = `${url}/preview/${encodedPath}`;

  const checkPreview = useCallback(async () => {
    setPreviewState("checking");
    setPreviewError(null);
    try {
      const health = await fetch(`${url}/preview/health`);
      if (!health.ok) throw new Error(`Preview service returned ${health.status}`);

      const file = await fetch(previewUrl);
      if (!file.ok) {
        let detail = `Preview file not found: ${filePath}`;
        try {
          const body = await file.json() as { error?: string; path?: string };
          if (body.error && body.error !== "not found") detail = body.error;
        } catch {
          // Keep the user-facing fallback when the response is not JSON.
        }
        throw new Error(detail);
      }
      setPreviewState("ready");
    } catch (error) {
      setPreviewError(error instanceof Error ? error.message : String(error));
      setPreviewState("error");
    }
  }, [filePath, previewUrl, url]);

  useEffect(() => {
    void checkPreview();
  }, [checkPreview, reloadCounter]);

  // SSE connection to the preview server for hot-reload.
  useEffect(() => {
    const es = new EventSource(`${url}/preview/events`);
    eventSourceRef.current = es;

    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        // Only reload if the changed file matches common web extensions.
        const ext = data.path?.split(".").pop()?.toLowerCase() ?? "";
        const webExts = ["html", "htm", "css", "js", "ts", "jsx", "tsx", "svg", "png", "jpg", "jpeg", "gif", "json"];
        if (webExts.includes(ext)) {
          reload();
        }
      } catch {
        // Ignore malformed events.
      }
    };

    return () => {
      es.close();
      eventSourceRef.current = null;
      setConnected(false);
    };
  }, [url, setConnected, reload]);

  const handlePathSubmit = useCallback(
    (e: React.FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      const form = new FormData(e.currentTarget);
      const path = form.get("filePath") as string;
      if (path?.trim()) {
        setFilePath(path.trim());
        reload();
      }
    },
    [setFilePath, reload],
  );

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-minimax-border bg-minimax-panel px-3">
        <form onSubmit={handlePathSubmit} className="flex flex-1 items-center gap-2">
          <input
            name="filePath"
            defaultValue={filePath}
            placeholder="e.g. index.html"
            data-testid="preview-path-input"
            className="h-6 flex-1 rounded border border-minimax-border bg-minimax-bg px-2 text-xs text-minimax-fg outline-none focus:border-minimax-accent"
          />
          <button
            type="submit"
            data-testid="preview-go-btn"
            className="rounded px-2 py-0.5 text-xs text-minimax-fg hover:bg-minimax-border"
          >
            Go
          </button>
        </form>
        <button
          type="button"
          data-testid="preview-reload-btn"
          onClick={reload}
          className="flex h-6 w-6 items-center justify-center rounded text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
          title="Reload preview"
        >
          <RefreshCw size={13} />
        </button>
        <div className="flex items-center gap-1 text-xs">
          {connected ? (
            <Wifi size={12} className="text-status-success" />
          ) : (
            <WifiOff size={12} className="text-minimax-muted" />
          )}
        </div>
        <button
          type="button"
          data-testid="preview-close-btn"
          onClick={onClose}
          className="flex h-6 w-6 items-center justify-center rounded text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
          title="Close preview"
        >
          <X size={13} />
        </button>
      </div>

      <div className="min-h-0 flex-1">
        {previewState === "checking" ? (
          <div
            data-testid="preview-loading"
            className="flex h-full items-center justify-center text-minimax-muted"
            aria-label="Loading preview"
          >
            <Loader2 size={18} className="animate-spin" />
          </div>
        ) : previewState === "error" ? (
          <div
            data-testid="preview-error"
            className="flex h-full items-center justify-center px-6"
          >
            <div className="max-w-md text-center">
              <TriangleAlert size={24} className="mx-auto text-status-warning" />
              <p className="mt-3 break-words text-sm text-minimax-fg">{previewError}</p>
              <button
                type="button"
                data-testid="preview-retry-btn"
                onClick={() => void checkPreview()}
                className="mt-4 rounded-md border border-minimax-border px-3 py-1.5 text-xs text-minimax-fg hover:border-minimax-accent/50 hover:bg-minimax-border/50"
              >
                Retry
              </button>
            </div>
          </div>
        ) : (
          <iframe
            key={`${previewUrl}:${reloadCounter}`}
            src={previewUrl}
            data-testid="preview-iframe"
            sandbox="allow-scripts allow-same-origin"
            className="h-full w-full border-0 bg-white"
            title="Live Preview"
          />
        )}
      </div>
    </div>
  );
}

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
import { useEffect, useRef, useCallback } from "react";
import { RefreshCw, X, Wifi, WifiOff } from "lucide-react";
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

  const iframeRef = useRef<HTMLIFrameElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  // Build the full preview URL.
  const previewUrl = `${url}/preview/${filePath}`;

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

  // Reload iframe when reloadCounter changes.
  useEffect(() => {
    if (iframeRef.current) {
      // eslint-disable-next-line no-self-assign
      iframeRef.current.src = iframeRef.current.src;
    }
  }, [reloadCounter]);

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

      {/* Iframe */}
      <div className="flex-1">
        <iframe
          ref={iframeRef}
          src={previewUrl}
          data-testid="preview-iframe"
          sandbox="allow-scripts allow-same-origin"
          className="h-full w-full border-0 bg-white"
          title="Live Preview"
        />
      </div>
    </div>
  );
}

import { useEffect } from "react";
import { ChatPanel, MessageInput, ProgressPanel, Sidebar } from "./components";
import { useChat } from "./stores";

/**
 * Top-level layout. Bootstraps the chat store on mount — the store's
 * `init` registers Tauri event listeners and probes the agent.
 */
export default function App() {
  const init = useChat((s) => s.init);
  const agentReady = useChat((s) => s.agentReady);

  useEffect(() => {
    init().catch((err) => {
      // The store will surface the error in `error`; logging here is
      // mostly so a developer running `pnpm dev:web` (browser-only)
      // sees why the round-trip is failing.
      // eslint-disable-next-line no-console
      console.warn("chat init failed:", err);
    });
  }, [init]);

  return (
    <div className="flex h-full w-full">
      <Sidebar />
      <main className="flex flex-1 flex-col">
        <ProgressPanel />
        <ChatPanel />
        <MessageInput />
      </main>
      {!agentReady && (
        <div className="pointer-events-none absolute bottom-2 right-2 rounded bg-minimax-panel/80 px-2 py-1 text-xs text-minimax-muted">
          {typeof window !== "undefined" && "__TAURI_INTERNALS__" in window
            ? "connecting to agent…"
            : "browser-only mode (no Tauri shell) — IPC will be mocked"}
        </div>
      )}
    </div>
  );
}

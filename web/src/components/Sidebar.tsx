import { useChat } from "../stores";

/**
 * Skeleton sidebar — later tasks will fill in sessions, skills,
 * scheduler, etc. For now it's a single button to clear the chat.
 */
export function Sidebar() {
  const reset = useChat((s) => s.reset);
  return (
    <aside className="w-56 shrink-0 border-r border-minimax-border bg-minimax-panel p-3">
      <div className="mb-4">
        <h1 className="text-sm font-semibold">MiniMax Code</h1>
        <p className="text-xs text-minimax-muted">Skeleton (Phase 1.0)</p>
      </div>
      <nav className="space-y-1 text-sm">
        <button
          onClick={reset}
          className="w-full rounded px-2 py-1.5 text-left hover:bg-minimax-border"
        >
          + New task
        </button>
        <div className="mt-4 space-y-1 text-minimax-muted">
          <div className="px-2 py-1 text-xs uppercase tracking-wider">
            Sections
          </div>
          <div className="rounded px-2 py-1 text-xs italic opacity-60">
            技能 (skills)
          </div>
          <div className="rounded px-2 py-1 text-xs italic opacity-60">
            定时任务
          </div>
          <div className="rounded px-2 py-1 text-xs italic opacity-60">
            连接手机
          </div>
          <div className="rounded px-2 py-1 text-xs italic opacity-60">
            任务历史
          </div>
          <div className="rounded px-2 py-1 text-xs italic opacity-60">
            Agents
          </div>
        </div>
      </nav>
    </aside>
  );
}

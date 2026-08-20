/**
 * Default session titles — shared by every call site that creates a
 * session (Sidebar, ChatPanel, command palette, stores, mock backend)
 * and by the auto-rename comparison in the chat store, so the data
 * default and its equality check can never drift apart.
 */
export const DEFAULT_SESSION_TITLE = "新任务";
export const DEFAULT_WORKTREE_TITLE = "Worktree 任务";

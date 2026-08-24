/**
 * Session store — owns the list of known sessions, the current
 * session id, project organization, and the section filters for the
 * sidebar. The chat store reads `currentSessionId` when sending messages.
 */

import { create } from "zustand";
import { typedIPC } from "../ipc";
import { toast } from "../components/layout/ErrorBoundary";
import type { Project, Session } from "../types/ipc";
import { useChat } from "./chat";
import { DEFAULT_SESSION_TITLE, DEFAULT_WORKTREE_TITLE } from "../lib/defaultTitles";
import { strings } from "../ui/strings";

export type SessionFilter =
  | "all"
  | "scheduled"
  | "history"
  | "agents"
  | "archived"
  | "skills";

export type SessionMeta = Session;

const CURRENT_SESSION_STORAGE_KEY = "minimax-code:current-session";
const CURRENT_PROJECT_STORAGE_KEY = "minimax-code:current-project";
const EXPANDED_PROJECTS_STORAGE_KEY = "minimax-code:expanded-projects";
let createSessionInFlight: Promise<string> | null = null;
let refreshSeq = 0;

/**
 * List hygiene: drop background subagent sessions (title "subagent:*",
 * created by handlers_agents for @-mentions and team runs) and strip the
 * "chat:*" bootstrap prefix the send_message handler leaves on pre-created
 * sessions. Both would otherwise surface in the sidebar task list.
 */
export function sanitizeSessions(list: SessionMeta[]): SessionMeta[] {
  return list
    .filter((s) => !s.title.startsWith("subagent:"))
    .map((s) =>
      s.title.startsWith("chat:")
        ? { ...s, title: s.title.slice("chat:".length).trim() || DEFAULT_SESSION_TITLE }
        : s,
    );
}

function readStoredSessionId(): string | null {
  try {
    return window.localStorage.getItem(CURRENT_SESSION_STORAGE_KEY);
  } catch {
    return null;
  }
}

function storeCurrentSessionId(id: string | null): void {
  try {
    if (id) window.localStorage.setItem(CURRENT_SESSION_STORAGE_KEY, id);
    else window.localStorage.removeItem(CURRENT_SESSION_STORAGE_KEY);
  } catch {
    // The in-memory selection still works when storage is unavailable.
  }
}

function readStoredProjectId(): string | null {
  try {
    return window.localStorage.getItem(CURRENT_PROJECT_STORAGE_KEY);
  } catch {
    return null;
  }
}

function storeCurrentProjectId(id: string | null): void {
  try {
    if (id) window.localStorage.setItem(CURRENT_PROJECT_STORAGE_KEY, id);
    else window.localStorage.removeItem(CURRENT_PROJECT_STORAGE_KEY);
  } catch {
    // ignore
  }
}

function readExpandedProjectIds(): string[] {
  try {
    const raw = window.localStorage.getItem(EXPANDED_PROJECTS_STORAGE_KEY);
    if (!raw) return ["inbox"];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : ["inbox"];
  } catch {
    return ["inbox"];
  }
}

function storeExpandedProjectIds(ids: string[]): void {
  try {
    window.localStorage.setItem(EXPANDED_PROJECTS_STORAGE_KEY, JSON.stringify(ids));
  } catch {
    // ignore
  }
}

export interface SessionState {
  sessions: SessionMeta[];
  projects: Project[];
  currentSessionId: string | null;
  currentProjectId: string | null;
  expandedProjectIds: string[];
  loading: boolean;
  loadingProjects: boolean;
  creating: boolean;
  filter: SessionFilter;
  selectedSessionIds: Set<string>;

  refresh: (options?: { loadCurrent?: boolean }) => Promise<void>;
  loadProjects: () => Promise<void>;
  create: (title?: string, projectId?: string) => Promise<string>;
  createWorktree: (title?: string, baseRef?: string) => Promise<string>;
  archive: (id: string) => Promise<void>;
  unarchive: (id: string) => Promise<void>;
  remove: (id: string) => Promise<void>;
  rename: (id: string, title: string) => Promise<void>;
  moveSessionProject: (id: string, projectId: string) => Promise<void>;
  mergeSessions: (sessions: SessionMeta[]) => void;
  setCurrent: (id: string | null, loadMessages?: boolean) => void;
  setFilter: (filter: SessionFilter) => void;
  setCurrentProject: (id: string | null) => void;
  toggleProjectExpanded: (id: string) => void;
  createProject: (name: string, description?: string, rootPath?: string) => Promise<Project | null>;
  updateProject: (id: string, fields: { name?: string; description?: string; root_path?: string }) => Promise<void>;
  deleteProject: (id: string) => Promise<void>;
  archiveProject: (id: string) => Promise<void>;
  unarchiveProject: (id: string) => Promise<void>;
  selectSession: (id: string, selected?: boolean) => void;
  toggleSessionSelection: (id: string) => void;
  clearSessionSelection: () => void;
  selectAllVisible: (ids: string[]) => void;
  batchArchiveSessions: (ids: string[], archived: boolean) => Promise<void>;
  batchMoveToProject: (ids: string[], projectId: string) => Promise<void>;
}

export const useSessionStore = create<SessionState>((set, get) => ({
  sessions: [],
  projects: [],
  currentSessionId: readStoredSessionId(),
  currentProjectId: readStoredProjectId(),
  expandedProjectIds: readExpandedProjectIds(),
  loading: false,
  loadingProjects: false,
  creating: false,
  filter: "all",
  selectedSessionIds: new Set(),

  refresh: async (options) => {
    const seq = ++refreshSeq;
    set({ loading: true });
    try {
      const [sessionsResult, projectsResult] = await Promise.all([
        typedIPC.listSessions(),
        typedIPC.listProjects(),
      ]);
      if (seq !== refreshSeq) {
        set({ loading: false });
        return;
      }
      const currentId = get().currentSessionId;
      // Existence is checked against the raw list: a subagent session can
      // legitimately stay "current" (its messages still load) even though
      // the sidebar list filters it out.
      const currentExists = !!currentId && sessionsResult.sessions.some((session) => session.id === currentId);
      set({
        sessions: sanitizeSessions(sessionsResult.sessions),
        projects: projectsResult.projects,
        currentSessionId: currentExists ? currentId : null,
        loading: false,
      });
      if (!currentExists && currentId) {
        storeCurrentSessionId(null);
        useChat.getState().reset();
      }
      if (currentExists && options?.loadCurrent !== false) {
        void useChat.getState().loadMessages(currentId);
      }
    } catch (err) {
      set({ loading: false });
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.sessionsLoadFailed, message);
    }
  },

  loadProjects: async () => {
    set({ loadingProjects: true });
    try {
      const result = await typedIPC.listProjects();
      set({ projects: result.projects, loadingProjects: false });
    } catch (err) {
      set({ loadingProjects: false });
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.projectsLoadFailed, message);
    }
  },

  create: async (title?: string, projectId?: string) => {
    if (createSessionInFlight) return createSessionInFlight;

    const operation = (async () => {
      set({ creating: true });
      try {
        const reuseId = get().currentSessionId ?? undefined;
        const targetProject = projectId ?? get().currentProjectId ?? "inbox";
        const r = await typedIPC.createSession({
          title,
          reuse_empty_session_id: reuseId,
          project_id: targetProject,
        });
        ++refreshSeq;
        const nextSession = r.session ?? {
          id: r.session_id,
          title: title ?? DEFAULT_SESSION_TITLE,
          archived: false,
          project_id: targetProject,
          created_at: Date.now(),
          updated_at: Date.now(),
          model_id: null,
          workspace_mode: "local" as const,
        };
        set((s) => ({
          sessions: s.sessions.some((session) => session.id === r.session_id)
            ? s.sessions.map((session) =>
                session.id === r.session_id ? { ...session, ...nextSession } : session,
              )
            : [nextSession, ...s.sessions],
          currentSessionId: r.session_id,
          currentProjectId: targetProject,
          expandedProjectIds: s.expandedProjectIds.includes(targetProject)
            ? s.expandedProjectIds
            : [...s.expandedProjectIds, targetProject],
        }));
        storeCurrentSessionId(r.session_id);
        storeCurrentProjectId(targetProject);
        storeExpandedProjectIds(get().expandedProjectIds);
        useChat.getState().reset();
        return r.session_id;
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        toast.error(strings.chat.toast.sessionCreateFailed, message);
        throw err;
      } finally {
        set({ creating: false });
        createSessionInFlight = null;
      }
    })();

    createSessionInFlight = operation;
    return operation;
  },

  createWorktree: async (title?: string, baseRef?: string) => {
    try {
      // v1.3.0: the worktree session is filed under the currently
      // selected project so per-project scoping (git/codebase/tools)
      // applies to it — "inbox" only when nothing is selected.
      const projectId = get().currentProjectId ?? "inbox";
      const r = await typedIPC.createWorktreeSession({ title, base_ref: baseRef, project_id: projectId });
      ++refreshSeq;
      set((s) => ({
        sessions: [
          r.session ?? {
            id: r.session_id,
            title: title ?? DEFAULT_WORKTREE_TITLE,
            archived: false,
            project_id: projectId,
            created_at: Date.now(),
            updated_at: Date.now(),
            model_id: null,
            workspace_mode: "worktree",
            workspace_path: r.worktree_path ?? null,
            base_branch: baseRef ?? "HEAD",
          },
          ...s.sessions,
        ],
        currentSessionId: r.session_id,
      }));
      storeCurrentSessionId(r.session_id);
      useChat.getState().reset();
      return r.session_id;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.worktreeCreateFailed, message);
      throw err;
    }
  },

  archive: async (id: string) => {
    try {
      await typedIPC.archiveSession(id);
      set((s) => ({
        sessions: s.sessions.map((x) => (x.id === id ? { ...x, archived: true } : x)),
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.archiveFailed, message);
    }
  },

  unarchive: async (id: string) => {
    try {
      await typedIPC.unarchiveSession(id);
      set((s) => ({
        sessions: s.sessions.map((x) => (x.id === id ? { ...x, archived: false } : x)),
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.unarchiveFailed, message);
    }
  },

  remove: async (id: string) => {
    try {
      // Worktree sessions own an on-disk git worktree. Route through
      // workspace.delete_worktree (git worktree remove + rmtree fallback)
      // before dropping the DB row, or the directory leaks on disk.
      const session = get().sessions.find((x) => x.id === id);
      if (session?.workspace_mode === "worktree") {
        try {
          await typedIPC.deleteWorktree(id);
        } catch (cleanupErr) {
          // The directory may already be gone; still delete the row,
          // but surface that cleanup was skipped.
          const msg = cleanupErr instanceof Error ? cleanupErr.message : String(cleanupErr);
          toast.error(strings.toasts.worktreeCleanupDegraded, msg);
        }
      }
      await typedIPC.deleteSession(id);
      const removingCurrent = get().currentSessionId === id;
      set((s) => ({
        sessions: s.sessions.filter((x) => x.id !== id),
        currentSessionId: s.currentSessionId === id ? null : s.currentSessionId,
      }));
      if (removingCurrent) {
        storeCurrentSessionId(null);
        useChat.getState().reset();
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.sessionDeleteFailed, message);
    }
  },

  rename: async (id: string, title: string) => {
    try {
      const r = await typedIPC.updateSession(id, { title });
      if (r.session) {
        set((s) => ({
          sessions: s.sessions.map((x) =>
            x.id === id ? { ...x, title: r.session.title, updated_at: r.session.updated_at } : x,
          ),
        }));
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.renameFailed, message);
    }
  },

  moveSessionProject: async (id: string, projectId: string) => {
    try {
      const r = await typedIPC.updateSessionProject(id, projectId);
      if (r.session) {
        set((s) => ({
          sessions: s.sessions.map((x) =>
            x.id === id
              ? { ...x, project_id: r.session.project_id, updated_at: r.session.updated_at }
              : x,
          ),
          // Expand the destination project so the moved row is visible.
          expandedProjectIds: s.expandedProjectIds.includes(projectId)
            ? s.expandedProjectIds
            : [...s.expandedProjectIds, projectId],
        }));
        storeExpandedProjectIds(get().expandedProjectIds);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.moveProjectFailed, message);
    }
  },

  mergeSessions: (nextSessions) => {
    if (nextSessions.length === 0) return;
    set((s) => {
      const byId = new Map(s.sessions.map((session) => [session.id, session]));
      for (const session of sanitizeSessions(nextSessions)) {
        byId.set(session.id, { ...byId.get(session.id), ...session });
      }
      return {
        sessions: Array.from(byId.values()).sort((a, b) => b.updated_at - a.updated_at),
      };
    });
  },

  setCurrent: (id: string | null, loadMessages = true) => {
    ++refreshSeq;
    set({ currentSessionId: id });
    storeCurrentSessionId(id);
    useChat.getState().reset();
    // When switching sessions, load the persisted messages for
    // the new session so the chat panel shows the history.
    if (id && loadMessages) {
      void useChat.getState().loadMessages(id);
    }
  },

  setFilter: (filter: SessionFilter) => set({ filter }),

  setCurrentProject: (id: string | null) => {
    set({ currentProjectId: id });
    storeCurrentProjectId(id);
  },

  toggleProjectExpanded: (id: string) => {
    set((s) => {
      const next = s.expandedProjectIds.includes(id)
        ? s.expandedProjectIds.filter((x) => x !== id)
        : [...s.expandedProjectIds, id];
      storeExpandedProjectIds(next);
      return { expandedProjectIds: next };
    });
  },

  createProject: async (name: string, description?: string, rootPath?: string) => {
    try {
      const r = await typedIPC.createProject({
        name,
        description,
        root_path: rootPath?.trim() ? rootPath.trim() : undefined,
      });
      set((s) => ({
        projects: [r.project, ...s.projects],
        expandedProjectIds: [...s.expandedProjectIds, r.project.id],
      }));
      return r.project;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.projectCreateFailed, message);
      return null;
    }
  },

  updateProject: async (id: string, fields: { name?: string; description?: string; root_path?: string }) => {
    try {
      const r = await typedIPC.updateProject(id, fields);
      if (r.project) {
        set((s) => ({
          projects: s.projects.map((p) =>
            p.id === id
              ? {
                  ...p,
                  name: r.project.name,
                  description: r.project.description,
                  root_path: r.project.root_path,
                  updated_at: r.project.updated_at,
                }
              : p,
          ),
        }));
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.projectUpdateFailed, message);
    }
  },

  deleteProject: async (id: string) => {
    try {
      await typedIPC.deleteProject(id);
      set((s) => ({
        projects: s.projects.filter((p) => p.id !== id),
        sessions: s.sessions.map((x) => (x.project_id === id ? { ...x, project_id: "inbox" } : x)),
        expandedProjectIds: s.expandedProjectIds.filter((x) => x !== id),
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.projectDeleteFailed, message);
    }
  },

  archiveProject: async (id: string) => {
    try {
      const r = await typedIPC.archiveProject(id);
      if (r.project) {
        set((s) => ({
          projects: s.projects.map((p) => (p.id === id ? { ...p, archived: true, updated_at: r.project.updated_at } : p)),
          expandedProjectIds: s.expandedProjectIds.filter((x) => x !== id),
        }));
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.projectArchiveFailed, message);
    }
  },

  unarchiveProject: async (id: string) => {
    try {
      const r = await typedIPC.unarchiveProject(id);
      if (r.project) {
        set((s) => ({
          projects: s.projects.map((p) => (p.id === id ? { ...p, archived: false, updated_at: r.project.updated_at } : p)),
        }));
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.projectUnarchiveFailed, message);
    }
  },

  selectSession: (id: string, selected = true) => {
    set((s) => {
      const next = new Set(s.selectedSessionIds);
      if (selected) next.add(id);
      else next.delete(id);
      return { selectedSessionIds: next };
    });
  },

  toggleSessionSelection: (id: string) => {
    set((s) => {
      const next = new Set(s.selectedSessionIds);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { selectedSessionIds: next };
    });
  },

  clearSessionSelection: () => set({ selectedSessionIds: new Set() }),

  selectAllVisible: (ids: string[]) => set({ selectedSessionIds: new Set(ids) }),

  batchArchiveSessions: async (ids: string[], archived: boolean) => {
    if (ids.length === 0) return;
    try {
      const r = await typedIPC.batchArchiveSessions(ids, archived);
      set((s) => {
        const byId = new Map(s.sessions.map((x) => [x.id, x]));
        for (const session of r.sessions) {
          byId.set(session.id, { ...(byId.get(session.id) ?? session), ...session });
        }
        return {
          sessions: Array.from(byId.values()).sort((a, b) => b.updated_at - a.updated_at),
          selectedSessionIds: new Set(),
        };
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.batchArchiveFailed, message);
    }
  },

  batchMoveToProject: async (ids: string[], projectId: string) => {
    if (ids.length === 0) return;
    try {
      const r = await typedIPC.batchUpdateSessionProject(ids, projectId);
      set((s) => {
        const byId = new Map(s.sessions.map((x) => [x.id, x]));
        for (const session of r.sessions) {
          byId.set(session.id, { ...(byId.get(session.id) ?? session), ...session });
        }
        return {
          sessions: Array.from(byId.values()).sort((a, b) => b.updated_at - a.updated_at),
          expandedProjectIds: s.expandedProjectIds.includes(projectId)
            ? s.expandedProjectIds
            : [...s.expandedProjectIds, projectId],
          selectedSessionIds: new Set(),
        };
      });
      storeExpandedProjectIds(get().expandedProjectIds);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.batchMoveFailed, message);
    }
  },

}));

/**
 * Per-project scoping for the git / codebase / patch-preview stores
 * and the worktree creator (v1.3.0).
 *
 * Every store action snapshots ``sessionStore.currentProjectId`` at
 * call time and injects it into the wire params so the backend can
 * resolve the project's workspace root. These tests pin that contract
 * end-to-end: the *real* ``typed.ts`` bindings run against a spied
 * ``client.request``, so each assertion covers both layers — the typed
 * wire shape (method + params) and the store-side injection.
 *
 * The null-currentProjectId cases pin the legacy branch: no
 * ``project_id`` key on the wire at all (the backend then falls back
 * to the process root, preserving pre-v1.3.0 behaviour).
 */

import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";

// Replace only the ``ipc`` singleton with a spy-backed instance and
// re-bind ``typedIPC`` onto it. Everything else in the module (the
// IPCClient class, bindTypedIPC, fetchHealth, ...) is kept real.
vi.mock("../../ipc/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../ipc/client")>();

  const wireByMethod: Record<string, unknown> = {
    "git.status": { repo_root: "/tmp", branch: "main", ahead: 0, behind: 0, files: [] },
    "git.diff": { files: [] },
    "git.log": { commits: [] },
    "codebase.status": { state: "idle" },
    "codebase.build_index": { state: "indexing" },
    "codebase.search": { results: [] },
    "codebase.summarize": { summary: "" },
    "patch.preview": { files: [] },
    "patch.apply_hunk": { ok: true },
    "patch.save_snapshot": { ok: true, snapshot_id: "snap_1" },
    "workspace.create_worktree_session": { session_id: "wt_1", worktree_path: "/tmp/wt" },
  };

  const spyClient = {
    request: vi.fn().mockImplementation((method: string) =>
      Promise.resolve(wireByMethod[method] ?? {}),
    ),
    start: vi.fn().mockResolvedValue(undefined),
    ping: vi.fn().mockResolvedValue(true),
    on: vi.fn(() => () => {}),
    close: vi.fn(),
  };

  return { ...mod, ipc: spyClient, typedIPC: mod.bindTypedIPC(spyClient as never) };
});

import { ipc } from "../../ipc/client";
import { useGitStore } from "../git";
import { useCodebaseStore } from "../codebaseStore";
import { usePatchPreviewStore } from "../patchPreviewStore";
import { useSessionStore } from "../sessionStore";

/** The spied request fn (the mocked ``ipc`` singleton is a plain object). */
const requestSpy = (ipc as unknown as { request: ReturnType<typeof vi.fn> }).request;

function setProject(pid: string | null): void {
  useSessionStore.setState({ currentProjectId: pid });
}

/** Last wire params sent for ``method`` (throws if never called). */
function lastParams(method: string): Record<string, unknown> {
  const calls = requestSpy.mock.calls.filter(([m]) => m === method) as [
    string,
    Record<string, unknown>?,
  ][];
  if (calls.length === 0) throw new Error(`no wire call for ${method}`);
  return calls[calls.length - 1][1] ?? {};
}

beforeAll(() => {
  // createWorktree persists the current session id — keep jsdom storage quiet.
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {});
});

beforeEach(() => {
  requestSpy.mockClear();
  setProject(null);
});

describe("git store project scoping (v1.3.0)", () => {
  it("refreshStatus injects project_id when a project is selected", async () => {
    setProject("pA");
    await useGitStore.getState().refreshStatus();
    expect(lastParams("git.status")).toEqual({ project_id: "pA" });
  });

  it("fetchDiff / fetchLog merge project_id alongside their own opts", async () => {
    setProject("pB");
    await useGitStore.getState().fetchDiff({ scope: "staged" });
    await useGitStore.getState().fetchLog({ n: 5 });
    expect(lastParams("git.diff")).toEqual({ scope: "staged", project_id: "pB" });
    expect(lastParams("git.log")).toEqual({ n: 5, project_id: "pB" });
  });

  it("omits the project_id key entirely when nothing is selected (legacy branch)", async () => {
    setProject(null);
    await useGitStore.getState().refreshStatus();
    expect(lastParams("git.status")).toEqual({});
  });
});

describe("codebase store project scoping (v1.3.0)", () => {
  it("status / build / search / summarize all carry the project_id", async () => {
    setProject("pA");
    const s = useCodebaseStore.getState();
    await s.refreshStatus();
    await s.buildIndex(true);
    await s.search("handler");
    await s.summarize("src/main.py");
    expect(lastParams("codebase.status")).toEqual({ project_id: "pA" });
    expect(lastParams("codebase.build_index")).toEqual({ force: true, project_id: "pA" });
    expect(lastParams("codebase.search")).toHaveProperty("project_id", "pA");
    expect(lastParams("codebase.summarize")).toEqual({ path: "src/main.py", project_id: "pA" });
  });

  it("search leaves project_id undefined when no project is selected", async () => {
    setProject(null);
    await useCodebaseStore.getState().search("anything");
    // codebase.* bindings build params explicitly (file_pattern, limit,
    // ... keys always present) — the legacy branch is an undefined
    // value, which JSON.stringify drops before it reaches the wire.
    expect(lastParams("codebase.search").project_id).toBeUndefined();
  });
});

describe("patch preview store project scoping (v1.3.0)", () => {
  it("preview / applyHunk / saveSnapshot carry the project_id", async () => {
    setProject("pA");
    const s = usePatchPreviewStore.getState();
    await s.refresh({ scope: "working" });
    await s.applyHunk({ file_path: "a.py", hunk_index: 0 });
    await s.saveSnapshot();
    expect(lastParams("patch.preview")).toEqual({ scope: "working", project_id: "pA" });
    expect(lastParams("patch.apply_hunk")).toHaveProperty("project_id", "pA");
    expect(lastParams("patch.save_snapshot")).toEqual({ project_id: "pA" });
  });
});

describe("createWorktree files the new session under the current project (v1.3.0)", () => {
  it("passes the selected project_id on the wire and into the fallback session meta", async () => {
    setProject("pA");
    const id = await useSessionStore.getState().createWorktree("feat/x", "main");
    expect(id).toBe("wt_1");
    expect(lastParams("workspace.create_worktree_session")).toEqual({
      title: "feat/x",
      base_ref: "main",
      project_id: "pA",
    });
    // The optimistic fallback meta (the wire result in this mock
    // carries no session object) is filed under the same project.
    const created = useSessionStore.getState().sessions.find((s) => s.id === "wt_1");
    expect(created?.project_id).toBe("pA");
  });

  it("falls back to inbox when no project is selected", async () => {
    setProject(null);
    await useSessionStore.getState().createWorktree();
    expect(lastParams("workspace.create_worktree_session")).toEqual({
      title: undefined,
      base_ref: undefined,
      project_id: "inbox",
    });
  });
});

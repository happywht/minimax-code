/**
 * Per-project re-rooting for the live preview (v1.7.1).
 *
 * ``previewStore.setRoot`` drives the ``preview.set_root`` wire method and
 * reloads the iframe; ``sessionStore`` wires it at every project-change
 * choke point (setCurrentProject / create / the one-shot boot refresh).
 * These tests pin the contract end-to-end against the *real* ``typed.ts``
 * bindings running on a spied ``client.request`` — same harness as
 * ``project-root-scope.test.ts``.
 */

import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";

// Replace only the ``ipc`` singleton with a spy-backed instance and
// re-bind ``typedIPC`` onto it; everything else stays real.
vi.mock("../../ipc/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../ipc/client")>();

  const spyClient = {
    request: vi.fn().mockImplementation((method: string) => {
      if (method === "preview.set_root") {
        return Promise.resolve({ ok: true, workspace: "/tmp/proj-root", project_id: "pA" });
      }
      if (method === "session.list") return Promise.resolve({ sessions: [] });
      if (method === "project.list") return Promise.resolve({ projects: [] });
      return Promise.resolve({});
    }),
    start: vi.fn().mockResolvedValue(undefined),
    ping: vi.fn().mockResolvedValue(true),
    on: vi.fn(() => () => {}),
    close: vi.fn(),
  };
  return { ...mod, ipc: spyClient, typedIPC: mod.bindTypedIPC(spyClient as never) };
});

import { ipc } from "../../ipc/client";
import { usePreviewStore } from "../previewStore";
import { useSessionStore } from "../sessionStore";

const requestSpy = (ipc as unknown as { request: ReturnType<typeof vi.fn> }).request;

function lastParams(method: string): Record<string, unknown> {
  const calls = requestSpy.mock.calls.filter(([m]) => m === method) as [
    string,
    Record<string, unknown>?,
  ][];
  if (calls.length === 0) throw new Error(`no wire call for ${method}`);
  return calls[calls.length - 1][1] ?? {};
}

beforeAll(() => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {});
});

beforeEach(() => {
  requestSpy.mockClear();
  usePreviewStore.setState({ rootProjectId: null, rootWorkspace: "", reloadCounter: 0 });
  useSessionStore.setState({ currentProjectId: null });
});

describe("previewStore.setRoot wire contract (v1.7.1)", () => {
  it("sends preview.set_root with the project_id and records the reply", async () => {
    await usePreviewStore.getState().setRoot("pA");
    expect(lastParams("preview.set_root")).toEqual({ project_id: "pA" });
    const s = usePreviewStore.getState();
    expect(s.rootProjectId).toBe("pA");
    expect(s.rootWorkspace).toBe("/tmp/proj-root");
    expect(s.reloadCounter).toBe(1);
  });

  it("omits the project_id key entirely for the process default (legacy branch)", async () => {
    await usePreviewStore.getState().setRoot(null);
    expect(lastParams("preview.set_root")).toEqual({});
    // The mock reply echoes pA regardless; what matters on the wire is
    // the empty params object — backend resolves the default root.
    expect(requestSpy).toHaveBeenCalled();
  });

  it("keeps the previous state (no reload) when the wire call fails", async () => {
    requestSpy.mockImplementationOnce(() => Promise.reject(new Error("boom")));
    await usePreviewStore.getState().setRoot("pA");
    const s = usePreviewStore.getState();
    expect(s.rootProjectId).toBeNull();
    expect(s.reloadCounter).toBe(0);
  });
});

describe("sessionStore wiring drives the preview re-root (v1.7.1)", () => {
  it("setCurrentProject re-roots the preview to the selected project", async () => {
    useSessionStore.getState().setCurrentProject("pA");
    // setRoot is fired void — let the microtask settle before asserting.
    await Promise.resolve();
    expect(lastParams("preview.set_root")).toEqual({ project_id: "pA" });
    expect(usePreviewStore.getState().rootProjectId).toBe("pA");
  });

  it("setCurrentProject(null) falls back to the process default root", async () => {
    useSessionStore.getState().setCurrentProject(null);
    await Promise.resolve();
    expect(lastParams("preview.set_root")).toEqual({});
  });

  it("the boot refresh syncs the preview root once", async () => {
    useSessionStore.setState({ currentProjectId: "pA" });
    await useSessionStore.getState().refresh();
    // The mock project list is empty, so the stale pA degrades to the
    // default root rather than erroring.
    expect(lastParams("preview.set_root")).toEqual({});
  });
});

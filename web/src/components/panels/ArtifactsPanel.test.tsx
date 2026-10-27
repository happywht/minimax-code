import { act, fireEvent, render, screen, waitFor, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TextArtifact } from "../../types/ipc";

const wire = vi.hoisted(() => ({ request: vi.fn(), host: "http://127.0.0.1:8765" }));
vi.mock("../../ipc", async () => {
  const { bindTypedIPC } = await import("../../ipc/typed");
  const ipc = { get agentBaseUrl() { return wire.host; }, isMock: false, ping: vi.fn(), request: wire.request };
  return { ipc, typedIPC: bindTypedIPC(ipc as never) };
});
vi.mock("../../stores", async () => {
  const { create } = await import("zustand");
  const { useArtifactStore } = await import("../../stores/artifactStore");
  const useSessionStore = create(() => ({ currentProjectId: "p", projects: [
    { id: "p", name: "Project", root_path: "/project" }, { id: "other", name: "Other", root_path: "/other" },
  ] }));
  return { useArtifactStore, useSessionStore };
});

import { ArtifactsPanel } from "./ArtifactsPanel";
import { useArtifactStore, useSessionStore } from "../../stores";

const row: TextArtifact = {
  id: `art_${"1".repeat(32)}`, project_id: "p", service_id: "svc_test", run_id: null,
  source_path: "reports/report.md", snapshot_id: "1".repeat(32), mime_type: "text/markdown",
  size_bytes: 30, sha256: "abc", version: 1, parse_status: "parseable", verification_status: "unverified", created_at: "now",
};
const preview = "<script>window.exploit = true</script>\n# Markdown";
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; };

beforeEach(() => {
  wire.host = "http://127.0.0.1:8765";
  wire.request.mockReset();
  useArtifactStore.getState().clear();
  useSessionStore.setState({ currentProjectId: "p" });
  wire.request.mockImplementation(async (method: string) => {
    if (method === "artifact.list") return { artifacts: [], service_id: "svc_test" };
    if (method === "artifact.register") return { artifact: row };
    if (method === "artifact.get") return { artifact: row, preview_text: preview };
    throw new Error("Unexpected method");
  });
});
afterEach(() => { cleanup(); useArtifactStore.getState().clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("project text artifacts", () => {
  it("registers relative path, shows escaped preview and unverified state, downloads authenticated original bytes", async () => {
    const createURL = vi.fn().mockReturnValue("blob:original");
    vi.stubGlobal("URL", { createObjectURL: createURL, revokeObjectURL: vi.fn() });
    const fetch = vi.fn().mockResolvedValue(new Response("original\r\n", { headers: { "X-Artifact-Service-Id": "svc_test", "X-Artifact-SHA256": row.sha256 } }));
    vi.stubGlobal("fetch", fetch);
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const { container } = render(<ArtifactsPanel onClose={vi.fn()} />);
    await screen.findByText("当前项目还没有登记的文本成果。");
    fireEvent.change(screen.getByLabelText("项目相对文件路径"), { target: { value: "reports/report.md" } });
    await waitFor(() => expect((screen.getByText("登记新版本") as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByText("登记新版本"));
    await screen.findByLabelText("成果纯文本预览");
    expect(wire.request).toHaveBeenCalledWith("artifact.register", { project_id: "p", source_path: "reports/report.md" });
    expect(wire.request).toHaveBeenCalledWith("artifact.get", { project_id: "p", artifact_id: row.id });
    expect(screen.getByLabelText("成果纯文本预览").textContent).toBe(preview);
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText(/v1.*可解析 \/ 未验证/)).toBeTruthy();
    fireEvent.click(screen.getByText("下载原始字节"));
    await waitFor(() => expect(click).toHaveBeenCalledOnce());
    expect(createURL).toHaveBeenCalledOnce();
    const [url, options] = fetch.mock.calls[0];
    expect(url).toBe(`http://127.0.0.1:8765/artifacts/${row.id}/content?project_id=p`);
    expect(url).not.toMatch(/token|auth/i);
    expect(options.credentials).toBe("include");
    expect(options.signal).toBeInstanceOf(AbortSignal);
  });

  it("shows loading/error and blocks registration until a service identity is loaded", async () => {
    const pending = deferred<{ artifacts: TextArtifact[]; service_id: string }>();
    wire.request.mockReturnValue(pending.promise);
    render(<ArtifactsPanel onClose={vi.fn()} />);
    expect(screen.getByText("正在读取成果…")).toBeTruthy();
    expect((screen.getByText("登记新版本") as HTMLButtonElement).disabled).toBe(true);
    await act(async () => pending.resolve({ artifacts: [], service_id: "svc_test" }));
    wire.request.mockRejectedValue(new Error("Vault missing"));
    fireEvent.click(screen.getByText("刷新"));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Vault missing");
  });

  it("discards a stale project list response", async () => {
    const pending = deferred<{ artifacts: TextArtifact[]; service_id: string }>();
    wire.request.mockImplementation((_method: string, params: { project_id: string }) => params.project_id === "p"
      ? pending.promise : Promise.resolve({ artifacts: [], service_id: "svc_other" }));
    render(<ArtifactsPanel onClose={vi.fn()} />);
    act(() => useSessionStore.setState({ currentProjectId: "other" }));
    await waitFor(() => expect(useArtifactStore.getState().serviceId).toBe("svc_other"));
    await act(async () => pending.resolve({ artifacts: [row], service_id: "svc_test" }));
    expect(useArtifactStore.getState().artifacts).toEqual([]);
    expect(screen.queryByText(/reports\/report.md/)).toBeNull();
  });

  it("discards a stale host response and rejects a foreign service/project artifact", async () => {
    const pending = deferred<{ artifacts: TextArtifact[]; service_id: string }>();
    wire.request.mockReturnValue(pending.promise);
    const active = useArtifactStore.getState().activate("p");
    wire.host = "http://127.0.0.1:9999";
    pending.resolve({ artifacts: [row], service_id: "svc_test" });
    await active;
    expect(useArtifactStore.getState().artifacts).toEqual([]);
    wire.request.mockResolvedValue({ artifacts: [row], service_id: "svc_other" });
    await useArtifactStore.getState().activate("p");
    expect(useArtifactStore.getState().error).toMatch(/association changed/);
    expect(useArtifactStore.getState().artifacts).toEqual([]);
  });

  it("rejects stale host actions before any RPC or download dispatch", async () => {
    await useArtifactStore.getState().activate("p");
    await useArtifactStore.getState().select(row.id);
    const selectedState = { ...useArtifactStore.getState() };
    wire.host = "http://127.0.0.1:9999";
    wire.request.mockClear();
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    for (const action of [() => useArtifactStore.getState().register("report.md"),
      () => useArtifactStore.getState().select(row.id), () => useArtifactStore.getState().download()]) {
      useArtifactStore.setState(selectedState);
      await action();
      expect(useArtifactStore.getState().error).toMatch(/context changed/);
    }
    expect(wire.request).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("ignores earlier preview when a later selection completes first", async () => {
    await useArtifactStore.getState().activate("p");
    const pending = deferred<{ artifact: TextArtifact; preview_text: string }>();
    wire.request.mockImplementation((_method: string, params: { artifact_id: string }) => params.artifact_id === row.id
      ? pending.promise : Promise.resolve({ artifact: { ...row, id: "new" }, preview_text: "new preview" }));
    const old = useArtifactStore.getState().select(row.id);
    await useArtifactStore.getState().select("new");
    pending.resolve({ artifact: row, preview_text: "old preview" });
    await old;
    expect(useArtifactStore.getState().preview).toBe("new preview");
  });

  it("aborts download and suppresses publication after project switch", async () => {
    await useArtifactStore.getState().activate("p");
    await useArtifactStore.getState().select(row.id);
    const pending = deferred<Response>();
    const fetch = vi.fn().mockReturnValue(pending.promise);
    vi.stubGlobal("fetch", fetch);
    const createURL = vi.fn();
    vi.stubGlobal("URL", { createObjectURL: createURL, revokeObjectURL: vi.fn() });
    const operation = useArtifactStore.getState().download();
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    useArtifactStore.getState().clear();
    expect(fetch.mock.calls[0][1].signal.aborted).toBe(true);
    pending.resolve(new Response("original", { headers: { "X-Artifact-Service-Id": "svc_test", "X-Artifact-SHA256": row.sha256 } }));
    await operation;
    expect(createURL).not.toHaveBeenCalled();
  });
});

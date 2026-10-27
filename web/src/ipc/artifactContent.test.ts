import { afterEach, describe, expect, it, vi } from "vitest";
import type { TextArtifact } from "../types/ipc";
const endpoint = vi.hoisted(() => ({ base: "" }));
vi.mock("./index", () => ({ ipc: { isMock: false, get agentBaseUrl() { return endpoint.base; } }, typedIPC: {} }));
import { fetchArtifactContent } from "./artifactContent";
import { rememberHealth } from "./httpAuth";

const artifact = { id: "art_123", project_id: "p", service_id: "svc_test", sha256: "abc" } as TextArtifact;
afterEach(() => { vi.unstubAllGlobals(); });
describe("artifact download authentication", () => {
  it("bootstraps the existing auth transport, sends bearer in headers, never a URL token", async () => {
    const base = "http://127.0.0.1:8871";
    endpoint.base = base;
    await rememberHealth(base, new Response(JSON.stringify({ auth_required: true })));
    const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ token: "ephemeral-secret" })))
      .mockResolvedValueOnce(new Response("original\r\n", { headers: { "X-Artifact-Service-Id": "svc_test", "X-Artifact-SHA256": "abc" } }));
    vi.stubGlobal("fetch", fetch);
    const blob = await fetchArtifactContent(artifact, base, new AbortController().signal);
    expect(blob.size).toBe(10);
    expect(fetch.mock.calls[0][0]).toBe(`${base}/auth/session`);
    expect(fetch.mock.calls[1][1].headers).toEqual({ Authorization: "Bearer ephemeral-secret" });
    expect(fetch.mock.calls[1][0]).not.toContain("ephemeral-secret");
    expect(fetch.mock.calls[1][0]).not.toMatch(/token|auth/i);
  });
  it("rejects a different service response before exposing a Blob", async () => {
    endpoint.base = "http://127.0.0.1:8872";
    const fetch = vi.fn().mockResolvedValue(new Response("different bytes", { headers: { "X-Artifact-Service-Id": "svc_other" } }));
    vi.stubGlobal("fetch", fetch);
    await expect(fetchArtifactContent(artifact, "http://127.0.0.1:8872", new AbortController().signal)).rejects.toThrow("service changed");
  });
  it("rejects a changed snapshot hash before exposing a Blob", async () => {
    endpoint.base = "http://127.0.0.1:8873";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("different bytes", {
      headers: { "X-Artifact-Service-Id": "svc_test", "X-Artifact-SHA256": "different" },
    })));
    await expect(fetchArtifactContent(artifact, "http://127.0.0.1:8873", new AbortController().signal)).rejects.toThrow("byte identity changed");
  });
});

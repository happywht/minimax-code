import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AttachmentOriginal } from "../types/ipc";
const endpoint = vi.hoisted(() => ({ base: "" }));
vi.mock("./index", () => ({ ipc: { isMock: false, get agentBaseUrl() { return endpoint.base; } }, typedIPC: {} }));
import { fetchAttachmentContent, uploadAttachment } from "./attachmentContent";
import { rememberHealth } from "./httpAuth";

const hash = "b8d718854b44be0166d2b9b4dfd13153d4b4b01d0cfe7f6311c5234e3612a4bc";
const row = { id: `att_${"1".repeat(32)}`, project_id: "p", service_id: "svc_test", sha256: hash, size_bytes: 10 } as AttachmentOriginal;
const headers = { "X-Attachment-Service-Id": row.service_id, "X-Attachment-SHA256": row.sha256 };
beforeEach(() => { endpoint.base = "http://127.0.0.1:8890"; vi.stubGlobal("crypto", webcrypto); });
afterEach(() => vi.unstubAllGlobals());

describe("authenticated attachment byte transport", () => {
  it("uploads raw bytes with required service epoch, filename only in escaped query and header-only bearer", async () => {
    endpoint.base = "http://127.0.0.1:8891";
    await rememberHealth(endpoint.base, new Response(JSON.stringify({ auth_required: true })));
    const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ token: "private-token" })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ attachment: row }), { status: 201 }));
    vi.stubGlobal("fetch", fetch);
    const file = new File(["original\r\n"], "<display>&.txt");
    expect(await uploadAttachment(file, "p", "svc_test", endpoint.base, new AbortController().signal)).toEqual(row);
    const [url, options] = fetch.mock.calls[1];
    expect(url).toContain("filename=%3Cdisplay%3E%26.txt");
    expect(url).not.toContain("private-token");
    expect(options.body).toBe(file);
    expect(options.headers).toEqual({ Authorization: "Bearer private-token", "Content-Type": "application/octet-stream", "X-Attachment-Service-Id": "svc_test" });
    expect(options.credentials).toBe("include");
  });
  it("rejects stale host/cancel before upload dispatch and oversized browser input", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    await expect(uploadAttachment(new File(["x"], "x.txt"), "p", "svc_test", "http://other", new AbortController().signal)).rejects.toThrow("endpoint changed");
    const controller = new AbortController(); controller.abort();
    await expect(uploadAttachment(new File(["x"], "x.txt"), "p", "svc_test", endpoint.base, controller.signal)).rejects.toThrow("cancelled");
    await expect(uploadAttachment({ name: "large.txt", size: 10485761 } as File, "p", "svc_test", endpoint.base, new AbortController().signal)).rejects.toThrow("10 MiB");
    expect(fetch).not.toHaveBeenCalled();
  });
  it("does not retry onto a changed host after auth bootstrap", async () => {
    endpoint.base = "http://127.0.0.1:8892";
    const base = endpoint.base;
    await rememberHealth(base, new Response(JSON.stringify({ auth_required: true })));
    const fetch = vi.fn().mockImplementation(async () => { endpoint.base = "http://127.0.0.1:9999"; return new Response(JSON.stringify({ token: "private" })); });
    vi.stubGlobal("fetch", fetch);
    await expect(uploadAttachment(new File(["x"], "x.txt"), "p", "svc_test", base, new AbortController().signal)).rejects.toThrow("endpoint changed");
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("checks original size and actual SHA-256 before exposing download bytes", async () => {
    const digest = await webcrypto.subtle.digest("SHA-256", new TextEncoder().encode("original\r\n"));
    const actualHash = Buffer.from(digest).toString("hex");
    const metadata = { ...row, sha256: actualHash };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("original\r\n", { headers: { ...headers, "X-Attachment-SHA256": actualHash } })));
    expect((await fetchAttachmentContent(metadata, endpoint.base, new AbortController().signal)).size).toBe(10);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("different!", { headers })));
    await expect(fetchAttachmentContent(row, endpoint.base, new AbortController().signal)).rejects.toThrow("byte hash changed");
  });
  it.each(["service", "hash", "short", "oversized"])("rejects %s response without publication", async (kind) => {
    const responseHeaders = { ...headers };
    if (kind === "service") responseHeaders["X-Attachment-Service-Id"] = "svc_replacement";
    if (kind === "hash") responseHeaders["X-Attachment-SHA256"] = "different";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(kind === "short" ? "x" : "x".repeat(11), { headers: responseHeaders })));
    await expect(fetchAttachmentContent(row, endpoint.base, new AbortController().signal)).rejects.toThrow(/changed/);
  });
});

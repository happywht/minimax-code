import { webcrypto } from "node:crypto";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { attachmentMock, mockAttachmentContent, storeMockAttachment } from "./attachmentMock";
import type { AttachmentListResult } from "../types/ipc";
import { mockRequest } from "./mock";
import { artifactMock } from "./artifactMock";

function file(text: string | Uint8Array, name: string): File {
  const bytes = typeof text === "string" ? new TextEncoder().encode(text) : text;
  return { name, size: bytes.byteLength, arrayBuffer: async () => new Uint8Array(bytes).buffer } as File;
}
beforeEach(() => vi.stubGlobal("crypto", webcrypto));
afterEach(() => vi.unstubAllGlobals());
describe("inert attachment demo contract", () => {
  it("versions independent originals, lists without preview, gets scoped escaped source, downloads original", async () => {
    const first = await storeMockAttachment(file("\ufeff<script>literal</script>\r\n=1+1", "same.txt"), "mock-p", () => {});
    const second = await storeMockAttachment(file("new", "same.txt"), "mock-p", () => {});
    expect(second.version).toBe(first.version + 1);
    const list = attachmentMock("attachment.list", { project_id: "mock-p" }, true) as AttachmentListResult;
    expect(list.service_id).toBe((artifactMock("artifact.list", { project_id: "mock-p" }, true) as { service_id: string }).service_id);
    expect(first.service_id).toBe(list.service_id);
    expect(list.attachments[0]).toEqual(second);
    expect(list.attachments[0]).not.toHaveProperty("preview_text");
    expect(attachmentMock("attachment.get", { project_id: "mock-p", attachment_id: first.id }, true)).toEqual({ attachment: first, preview_text: "\ufeff<script>literal</script>\r\n=1+1" });
    expect(mockAttachmentContent(first).size).toBe(first.size_bytes);
    expect(() => attachmentMock("attachment.get", { project_id: "foreign", attachment_id: first.id }, true)).toThrow("this project");
    expect(() => attachmentMock("attachment.list", { project_id: "mock-p", source_path: "host" }, true)).toThrow("Invalid");
    expect(() => attachmentMock("attachment.list", { project_id: "missing" }, false)).toThrow("existing project");
  });
  it.each([
    ["", "empty.txt", "empty"], ['"unfinished', "bad.csv", "parse_failed"], ["a\0b", "bad.txt", "parse_failed"],
    ["%PDF-1.7", "paper.txt", "type_mismatch"], ["%PDF-1.7", "paper.pdf", "parser_unavailable"],
    [new Uint8Array([137,80,78,71,13,10,26,10]), "image.png", "ocr_not_attempted"],
    [new Uint8Array([255,254]), "bad.txt", "parse_failed"], ["汉".repeat(30000), "large.txt", "truncated"],
    ["x\r".repeat(2001), "lines.txt", "truncated"],
  ])("retains parser case %# with explicit status", async (text, name, status) => {
    const row = await storeMockAttachment(file(text as string | Uint8Array, name as string), "states", () => {});
    expect(row.parser_status).toBe(status); expect(row.quality_status).toBe("unverified");
    const result = attachmentMock("attachment.get", { project_id: "states", attachment_id: row.id }, true) as { preview_text: string };
    expect(new TextEncoder().encode(result.preview_text).length).toBeLessThanOrEqual(65536);
    expect(result.preview_text).not.toContain("\ufffd");
    expect(mockAttachmentContent(row).size).toBe(row.size_bytes);
  });
  it("checks epoch before publishing delayed in-memory upload", async () => {
    const input = file("original", "stale.txt");
    await expect(storeMockAttachment(input, "stale", () => { throw new Error("stale epoch"); })).rejects.toThrow("stale epoch");
    expect((attachmentMock("attachment.list", { project_id: "stale" }, true) as AttachmentListResult).attachments).toEqual([]);
  });
  it("is registered in the real mock dispatcher and rejects missing/foreign params", async () => {
    const client = {} as Parameters<typeof mockRequest>[2];
    await expect(mockRequest("attachment.list", null, client)).rejects.toThrow("Invalid attachment parameters");
    await expect(mockRequest("attachment.list", { project_id: "missing-project" }, client)).rejects.toThrow("existing project");
  });
});

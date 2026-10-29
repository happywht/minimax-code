/** In-memory originals only. PDF/DOCX/XLSX parsing requires the real backend extra. */
import type { AttachmentGetResult, AttachmentKind, AttachmentOriginal, AttachmentParserStatus } from "../types/ipc";
import { MOCK_ARTIFACT_SERVICE } from "./artifactMock";
import { metadataAnchor, metadataCursor } from "./metadataCursor";

export const MOCK_ATTACHMENT_SERVICE = MOCK_ARTIFACT_SERVICE;
export const ATTACHMENT_LIMIT = 10 * 1024 * 1024;
const rows: { metadata: AttachmentOriginal; bytes: Uint8Array; preview: string }[] = [];
const kinds = new Set<AttachmentKind>(["txt", "csv", "pdf", "docx", "xlsx", "png", "jpeg", "gif", "webp", "unknown"]);

export function attachmentMock(method: string, params: Record<string, unknown>, projectExists: boolean): unknown {
  const allowed = method === "attachment.get" ? ["project_id", "attachment_id"] : ["project_id", "cursor", "query"];
  if (!params || typeof params !== "object" || Array.isArray(params)
      || Object.keys(params).some((key) => !allowed.includes(key))
      || typeof params.project_id !== "string" || !params.project_id || params.project_id.length > 128) throw new Error("Invalid attachment parameters");
  if (!projectExists) throw new Error("Select an existing project");
  if (method === "attachment.list") {
    if (params.query != null && (typeof params.query !== "string" || !params.query || [...params.query].length > 240)) throw new Error("Invalid query");
    const scope = ["att", params.project_id, MOCK_ATTACHMENT_SERVICE, null, params.query ?? null];
    const scoped = rows.filter((row) => row.metadata.project_id === params.project_id && (params.query == null || row.metadata.original_name.includes(params.query as string))).slice().reverse().map((row) => row.metadata);
    const anchor = metadataAnchor(params.cursor, scope, params.query);
    const index = anchor == null ? -1 : scoped.findIndex((row) => row.id === anchor);
    if (anchor != null && (!/^att_[0-9a-f]{32}$/.test(anchor) || index < 0)) throw new Error("Invalid attachment cursor");
    const page = scoped.slice(index + 1, index + 201);
    return { service_id: MOCK_ATTACHMENT_SERVICE, attachments: page,
      next_cursor: scoped.length > index + 201 ? (params.query == null ? page[page.length - 1].id : metadataCursor(scope, page[page.length - 1].id)) : null };
  }
  if (method !== "attachment.get" || typeof params.attachment_id !== "string" || !/^att_[a-f0-9]{32}$/.test(params.attachment_id)) throw new Error("Invalid attachment parameters");
  const row = rows.find((item) => item.metadata.id === params.attachment_id && item.metadata.project_id === params.project_id);
  if (!row) throw new Error("Attachment does not exist in this project");
  return { attachment: row.metadata, preview_text: row.preview } satisfies AttachmentGetResult;
}

function strictCSV(text: string): void {
  let quoted = false, atStart = true, closed = false, fieldBytes = 0;
  for (let index = 0; index < text.length; index++) {
    const char = text[index];
    if (++fieldBytes > 131072) throw new Error("Bounded CSV field exceeded");
    if (quoted) {
      if (char === '"') {
        if (text[index + 1] === '"') index++;
        else { quoted = false; closed = true; }
      }
    } else if (char === "," || char === "\r" || char === "\n") {
      atStart = true; closed = false; fieldBytes = 0;
    } else if (closed) throw new Error("Malformed CSV");
    else if (char === '"' && atStart) { quoted = true; atStart = false; }
    else atStart = false;
  }
  if (quoted) throw new Error("Malformed CSV");
}

function inspect(bytes: Uint8Array, declared: AttachmentKind): { recognized: AttachmentOriginal["recognized_type"]; status: AttachmentParserStatus; preview: string } {
  const starts = (...signature: number[]) => signature.every((value, index) => bytes[index] === value);
  let recognized: AttachmentOriginal["recognized_type"] = starts(0x25, 0x50, 0x44, 0x46, 0x2d) ? "pdf"
    : starts(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a) ? "png"
      : starts(0xff, 0xd8, 0xff) ? "jpeg"
        : starts(0x47, 0x49, 0x46, 0x38, 0x37, 0x61) || starts(0x47, 0x49, 0x46, 0x38, 0x39, 0x61) ? "gif"
          : starts(0x52, 0x49, 0x46, 0x46) && bytes[8] === 0x57 && bytes[9] === 0x45 && bytes[10] === 0x42 && bytes[11] === 0x50 ? "webp"
            : starts(0x50, 0x4b, 0x03, 0x04) ? "zip" : "binary";
  let text: string | null = null;
  if (recognized === "binary") {
    try { text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes); recognized = "utf8_text"; }
    catch { /* Invalid UTF-8 remains a retained original, without preview. */ }
  }
  if (declared === "txt" || declared === "csv") {
    if (!["utf8_text", "binary"].includes(recognized)) return { recognized, status: "type_mismatch", preview: "" };
    try {
      if (text == null || text.includes("\0")) throw new Error("Invalid text");
      if (declared === "csv") strictCSV(text);
      let preview = new TextDecoder("utf-8", { fatal: false, ignoreBOM: true }).decode(bytes.slice(0, 65536));
      if (preview.endsWith("\ufffd") && new TextEncoder().encode(text).length > 65536) {
        let end = Math.min(65536, bytes.length);
        while (end && (bytes[end] & 0xc0) === 0x80) end--;
        preview = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes.slice(0, end));
      }
      // Python splitlines boundaries, retained literally without evaluating content.
      // eslint-disable-next-line no-control-regex
      const lines = preview.match(/[^\r\n\v\f\x1c-\x1e\x85\u2028\u2029]*(?:\r\n|[\r\n\v\f\x1c-\x1e\x85\u2028\u2029])|[^\r\n\v\f\x1c-\x1e\x85\u2028\u2029]+$/g) ?? [];
      preview = lines.slice(0, 2000).join("");
      return { recognized, status: !text ? "empty" : preview !== text ? "truncated" : "parsed", preview };
    } catch { return { recognized, status: "parse_failed", preview: "" }; }
  }
  // Mock mode never claims to inspect Office containers; it retains those originals
  // as unavailable with a generic ZIP candidate label.
  if (["docx", "xlsx"].includes(declared) && recognized === "zip") return { recognized, status: "parser_unavailable", preview: "" };
  if (declared !== "unknown" && recognized !== declared) return { recognized, status: "type_mismatch", preview: "" };
  return { recognized, status: ["png", "jpeg", "gif", "webp"].includes(recognized) ? "ocr_not_attempted" : "parser_unavailable", preview: "" };
}

export async function storeMockAttachment(file: File, project: string, requireCurrent: () => void): Promise<AttachmentOriginal> {
  if (file.size > ATTACHMENT_LIMIT || !file.name.trim() || [...file.name].length > 240
    || [...file.name].some((char) => char.charCodeAt(0) < 32 || (char.charCodeAt(0) >= 127 && char.charCodeAt(0) <= 159))) throw new Error("Invalid attachment size or display filename");
  const bytes = new Uint8Array(await file.arrayBuffer());
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  requireCurrent();
  const leaf = file.name.replace(/\\/g, "/").split("/").pop() ?? "";
  const label = leaf.includes(".") ? leaf.split(".").pop()?.toLowerCase() ?? "unknown" : "unknown";
  const kind = label === "jpg" ? "jpeg" : label;
  const declared = kinds.has(kind as AttachmentKind) ? kind as AttachmentKind : "unknown";
  const parsed = inspect(bytes, declared);
  const id = String(rows.length + 1).padStart(32, "0");
  const metadata: AttachmentOriginal = {
    id: `att_${id}`, snapshot_id: id, project_id: project, service_id: MOCK_ATTACHMENT_SERVICE,
    original_name: file.name, declared_type: declared, recognized_type: parsed.recognized,
    size_bytes: bytes.byteLength, sha256: Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join(""),
    version: rows.filter((row) => row.metadata.project_id === project && row.metadata.original_name === file.name).length + 1,
    parser_status: parsed.status, parser_version: 2,
    parser_reason: parsed.status === "type_mismatch" ? "type_mismatch" : parsed.status === "parse_failed" ? "invalid_text" : parsed.status === "truncated" ? "preview_limit" : parsed.status === "parser_unavailable" ? "missing_dependency" : parsed.status === "ocr_not_attempted" ? "ocr_not_attempted" : "ok",
    parser_scope: declared === "txt" || declared === "csv" ? "inert_text" : "original_only", preview_truncated: parsed.status === "truncated",
    quality_status: "unverified", created_at: new Date().toISOString(),
  };
  rows.push({ metadata, bytes, preview: parsed.preview });
  return metadata;
}

export function mockAttachmentContent(row: AttachmentOriginal): Blob {
  const original = rows.find((item) => item.metadata.id === row.id && item.metadata.project_id === row.project_id
    && item.metadata.service_id === row.service_id && item.metadata.sha256 === row.sha256);
  if (!original) throw new Error("Attachment original unavailable in demo memory");
  return new Blob([new Uint8Array(original.bytes).buffer], { type: "application/octet-stream" });
}

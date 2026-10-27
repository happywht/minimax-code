import { ipc, typedIPC } from "./index";
import { authHeaders, ensureSession } from "./httpAuth";
import { ATTACHMENT_LIMIT, mockAttachmentContent, storeMockAttachment } from "./attachmentMock";
import type { AttachmentOriginal } from "../types/ipc";

function current(base: string, signal: AbortSignal): void {
  if (ipc.agentBaseUrl !== base || signal.aborted) throw new Error("Attachment endpoint changed or operation cancelled");
}

async function authenticated(base: string, signal: AbortSignal, url: string, options: RequestInit): Promise<Response> {
  current(base, signal);
  await ensureSession(base);
  current(base, signal);
  const dispatch = () => fetch(url, { ...options, signal, credentials: "include", headers: { ...authHeaders(base), ...options.headers } });
  let response = await dispatch();
  if (response.status === 401) {
    await ensureSession(base, true);
    current(base, signal);
    response = await dispatch();
  }
  current(base, signal);
  return response;
}

export async function uploadAttachment(file: File, project: string, service: string, base: string, signal: AbortSignal): Promise<AttachmentOriginal> {
  current(base, signal);
  if (file.size > ATTACHMENT_LIMIT || !file.name.trim() || [...file.name].length > 240) throw new Error("Attachment requires a display filename and at most 10 MiB");
  if (ipc.isMock) {
    const context = await typedIPC.listAttachments(project);
    current(base, signal);
    if (context.service_id !== service) throw new Error("Attachment service changed; refresh");
    return storeMockAttachment(file, project, () => current(base, signal));
  }
  const query = new URLSearchParams({ project_id: project, filename: file.name });
  const response = await authenticated(base, signal, `${base}/attachments?${query}`, {
    method: "POST", headers: { "Content-Type": "application/octet-stream", "X-Attachment-Service-Id": service }, body: file,
  });
  if (!response.ok) throw new Error(`Attachment upload failed (HTTP ${response.status}); refresh service/project if changed`);
  const result = await response.json() as { attachment: AttachmentOriginal };
  current(base, signal);
  return result.attachment;
}

export async function fetchAttachmentContent(row: AttachmentOriginal, base: string, signal: AbortSignal): Promise<Blob> {
  current(base, signal);
  if (ipc.isMock) return mockAttachmentContent(row);
  const response = await authenticated(base, signal,
    `${base}/attachments/${encodeURIComponent(row.id)}/content?project_id=${encodeURIComponent(row.project_id)}`, {});
  if (!response.ok) throw new Error(`Attachment download failed (HTTP ${response.status})`);
  if (response.headers.get("X-Attachment-Service-Id") !== row.service_id) throw new Error("Attachment service changed; refresh");
  if (response.headers.get("X-Attachment-SHA256") !== row.sha256) throw new Error("Attachment byte identity changed; refresh");
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Attachment download stream unavailable");
  const parts: Uint8Array[] = [];
  let size = 0;
  try {
    let complete = false;
    while (!complete) {
      const { done, value } = await reader.read();
      current(base, signal);
      if (done) { complete = true; break; }
      size += value.byteLength;
      if (size > row.size_bytes || size > ATTACHMENT_LIMIT) throw new Error("Attachment byte size changed; refresh");
      parts.push(value);
    }
  } catch (error) { await reader.cancel().catch(() => {}); throw error; }
  finally { reader.releaseLock(); }
  if (size !== row.size_bytes) throw new Error("Attachment byte size changed; refresh");
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const part of parts) { bytes.set(part, offset); offset += part.byteLength; }
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  current(base, signal);
  const hash = Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
  if (hash !== row.sha256) throw new Error("Attachment byte hash changed; refresh");
  return new Blob([bytes.buffer], { type: "application/octet-stream" });
}
